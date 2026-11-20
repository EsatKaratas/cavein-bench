"""Runs one agent through one scenario in one language, records everything, scores it."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

import yaml

from . import bifrost
from .config import (AGENT_LOG_DIR, AGENT_TIMEOUT_S, AGENTS_FILE, BIFROST_URL, MAX_ACTIONS_PER_TURN,
                     OUTAGE_MODEL, ROOT, SCENARIO_DIR)
from .scoring import deterministic_checks, final_verdict, judge
from .search import tavily_search
from .store import save_run
from .world import POLICY, TOOLS, World, tool_result_text


def load_scenarios() -> dict:
    out = {}
    for p in sorted(SCENARIO_DIR.glob("*.yaml")):
        s = yaml.safe_load(p.read_text(encoding="utf-8"))
        out[s["id"]] = s
    return out


def load_agents() -> dict:
    return yaml.safe_load(AGENTS_FILE.read_text(encoding="utf-8"))["agents"]


def _local_bin(name):
    """Fallback for tools installed under ~/.local (not always on a login shell's PATH)."""
    for d in ("~/.local/bin", "~/.local/node/bin", "/opt/homebrew/bin", "/usr/local/bin"):
        p = os.path.expanduser(os.path.join(d, name))
        if os.path.exists(p):
            return p
    return name


class AgentProcess:
    """An agent is any program that speaks JSON lines on stdin/stdout (docs/PROTOCOL.md)."""

    def __init__(self, cmd: list[str], log_name: str):
        cmd = [sys.executable if c == "python" else (shutil.which(c) or _local_bin(c)) if i == 0 else c
               for i, c in enumerate(cmd)]
        AGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.stderr = open(AGENT_LOG_DIR / f"{log_name}.log", "w", encoding="utf-8")
        self.p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.stderr, text=True, encoding="utf-8", bufsize=1, env=os.environ.copy())
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            line = line.strip()
            if line:
                self.lines.put(line)
        self.lines.put(None)

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def recv(self, timeout):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"agent sent nothing for {timeout}s")
        if line is None:
            raise EOFError("agent process exited")
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return {"type": "error", "message": f"agent printed non-JSON: {line[:200]}"}

    def close(self):
        try:
            self.send({"type": "end"})
            self.p.wait(timeout=3)
        except Exception:
            self.p.kill()
        self.stderr.close()


class Abort(Exception):
    pass


INFRA_PATTERNS = re.compile(r"\b(429|500|502|503|504)\b|RESOURCE_EXHAUSTED|timed out|Timeout|overloaded|UNAVAILABLE|"
                            r"cavein-model-that-does-not-exist|Connection(Error|Refused)", re.I)


def is_infra_error(msg: str) -> bool:
    return bool(INFRA_PATTERNS.search(msg or ""))


def as_text(content) -> str:
    """Some providers return message content as a list of parts. Normalize to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def run_one(agent_name: str, agent_cfg: dict, scenario: dict, lang: str, repeat: int, batch: str,
            use_judge: bool = True) -> dict:
    run_id = f"{batch}.{agent_name}.{scenario['id']}.{lang}.r{repeat}.{uuid.uuid4().hex[:5]}"
    fault = scenario.get("fault") or {}
    model, fallbacks = agent_cfg["model"], list(agent_cfg.get("fallbacks", []))
    if fault.get("kind") == "model_unavailable":
        # Primary model is gone. Only a gateway failover keeps this conversation alive.
        fallbacks = [model] + fallbacks
        model = OUTAGE_MODEL
    world = World(fault if fault.get("kind") == "tool_error" else None, web_search=tavily_search)

    events, replies, telemetry = [], [], []
    t_start = time.time()

    def ev(kind, payload):
        events.append({"t": time.time(), "kind": kind, "payload": payload})

    ev("start", {"run_id": run_id, "agent": agent_name, "model": model, "fallbacks": fallbacks,
                 "scenario": scenario["id"], "lang": lang, "fault": fault or None})
    proc = AgentProcess(agent_cfg["cmd"], run_id)
    crashed = None
    try:
        proc.send({"type": "reset", "run_id": run_id, "agent_name": agent_name, "policy": POLICY, "tools": TOOLS,
                   "model": model, "fallbacks": fallbacks, "bifrost_url": BIFROST_URL})
        for text in scenario["turns"][lang]:
            ev("customer", {"content": text})
            proc.send({"type": "customer", "content": text})
            actions = 0
            while True:
                msg = proc.recv(AGENT_TIMEOUT_S)
                telemetry.extend(msg.get("llm") or [])
                kind = msg.get("type")
                if kind == "tool_call":
                    actions += 1
                    if actions > MAX_ACTIONS_PER_TURN:
                        crashed = f"loop: more than {MAX_ACTIONS_PER_TURN} tool calls in one turn"
                        raise Abort
                    name, args = msg.get("name", ""), msg.get("arguments") or {}
                    ev("tool_call", {"call_id": msg.get("call_id"), "name": name, "arguments": args,
                                     "llm": msg.get("llm") or []})
                    res = world.call(name, args)
                    content = tool_result_text(res["result"])
                    ev("tool_result", {"call_id": msg.get("call_id"), "name": name, "content": content,
                                       "injected_fault": res["injected_fault"]})
                    proc.send({"type": "tool_result", "call_id": msg.get("call_id"), "name": name, "content": content})
                elif kind == "note":
                    ev("guard_note", {"content": msg.get("content", ""), "llm": msg.get("llm") or []})
                elif kind == "reply":
                    text = as_text(msg.get("content"))
                    ev("agent_reply", {"content": text, "llm": msg.get("llm") or []})
                    replies.append(text)
                    break
                else:
                    crashed = f"agent error: {msg.get('message', msg)}"[:400]
                    raise Abort
    except Abort:
        pass
    except (TimeoutError, EOFError, BrokenPipeError) as e:
        crashed = f"{e.__class__.__name__}: {e}"
    finally:
        proc.close()
    wall_ms = int((time.time() - t_start) * 1000)
    if crashed:
        ev("crash", {"reason": crashed})

    # Metrics: Bifrost logs are the source of truth; agent self-reports are the fallback.
    logs = bifrost.run_logs(run_id)
    if logs:
        m = bifrost.summarize_logs(logs)
        source = "bifrost"
        ev("bifrost_logs", {"count": len(logs), "entries": [
            {k: lg.get(k) for k in ("provider", "model", "status", "latency", "cost", "fallback_index",
                                    "number_of_retries", "token_usage", "timestamp")} for lg in logs]})
    else:
        m = {"llm_calls": len(telemetry), "tokens_in": sum(t.get("tokens_in", 0) for t in telemetry),
             "tokens_out": sum(t.get("tokens_out", 0) for t in telemetry), "cost": 0.0,
             "llm_latency_ms": sum(t.get("latency_ms", 0) for t in telemetry), "fallbacks": 0, "providers": {}}
        for t in telemetry:
            key = t.get("model") or "?"
            m["providers"][key] = m["providers"].get(key, 0) + 1
        source = "agent"

    effects = world.effects()
    checks = deterministic_checks(scenario["expect"], effects, replies, crashed)
    if use_judge and replies:
        verdict = judge(scenario, lang, events, run_id)
    elif not replies:
        verdict = {"skipped": "no replies to judge", "language_ok": None}
    else:
        verdict = {"skipped": "judge disabled"}
    passed, labels = final_verdict(checks, verdict if use_judge and replies else {})
    if use_judge and replies and "error" in verdict:
        passed = False
        labels = labels + ["judge_error"]
    if crashed and is_infra_error(crashed):
        # The provider failed after every Bifrost fallback (quota, overload, timeout).
        # That says nothing about the agent: keep the run for the record, exclude it from scores.
        passed, labels = False, ["infra_error"]
        crashed = "INFRA: " + crashed

    row = {
        "id": run_id, "batch": batch, "started_at": t_start, "agent": agent_name, "model": agent_cfg["model"],
        "scenario": scenario["id"], "pressure": scenario["pressure"],
        "fault": (fault or {}).get("kind") or "none", "lang": lang, "repeat": repeat,
        "passed": int(passed), "labels": labels, "checks": checks, "judge": verdict, "effects": effects,
        "steps": sum(1 for e in events if e["kind"] in ("tool_call", "agent_reply")),
        "tool_calls": effects["tool_calls"], "llm_calls": m["llm_calls"], "tokens_in": m["tokens_in"],
        "tokens_out": m["tokens_out"], "cost": round(m["cost"], 6), "llm_latency_ms": m["llm_latency_ms"],
        "wall_ms": wall_ms, "fallbacks": m["fallbacks"], "providers": m["providers"],
        "telemetry_source": source, "error": crashed or "",
    }
    save_run(row, events)
    return row
