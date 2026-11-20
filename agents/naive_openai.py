"""Reference agent #1: the simplest possible tool-calling agent, built on the OpenAI SDK.

It speaks the CaveIn protocol (JSON lines on stdin/stdout, see docs/PROTOCOL.md)
and sends every model call through Bifrost. It trusts the system prompt alone to
keep it inside the policy.
"""
import json
import os
import sys
import time

from openai import OpenAI


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Agent:
    def __init__(self, cfg):
        self.model = cfg["model"]
        self.fallbacks = cfg.get("fallbacks", [])
        self.tools = cfg["tools"]
        self.messages = [{"role": "system", "content": cfg["policy"]}]
        self.pending = []
        self.telemetry = []
        self.client = OpenAI(base_url=cfg["bifrost_url"] + "/v1", api_key="not-needed-bifrost-holds-keys",
                             max_retries=0, timeout=120)
        self.headers = {"x-bf-lh-run_id": cfg["run_id"], "x-bf-lh-agent": cfg.get("agent_name", "naive")}

    def llm(self):
        t0 = time.time()
        resp = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=self.tools, temperature=0.3,
            extra_body={"fallbacks": self.fallbacks}, extra_headers=self.headers)
        raw = resp.model_dump()
        u = raw.get("usage") or {}
        self.telemetry.append({
            "model": raw.get("model"), "extra": raw.get("extra_fields"),
            "tokens_in": u.get("prompt_tokens", 0), "tokens_out": u.get("completion_tokens", 0),
            "latency_ms": int((time.time() - t0) * 1000)})
        return resp.choices[0].message

    def flush_telemetry(self):
        t, self.telemetry = self.telemetry, []
        return t

    def act(self):
        msg = self.llm()
        entry = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            entry["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            self.messages.append(entry)
            self.pending = [tc for tc in msg.tool_calls]
            self.emit_next_tool()
        else:
            self.messages.append(entry)
            send({"type": "reply", "content": msg.content or "", "llm": self.flush_telemetry()})

    def emit_next_tool(self):
        tc = self.pending.pop(0)
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        send({"type": "tool_call", "call_id": tc.id, "name": tc.function.name, "arguments": args,
              "llm": self.flush_telemetry()})

    def on_tool_result(self, ev):
        self.messages.append({"role": "tool", "tool_call_id": ev["call_id"], "content": ev["content"]})
        if self.pending:
            self.emit_next_tool()
        else:
            self.act()


def main():
    agent = None
    for line in sys.stdin:
        ev = json.loads(line)
        try:
            if ev["type"] == "reset":
                agent = Agent(ev)
            elif ev["type"] == "customer":
                agent.messages.append({"role": "user", "content": ev["content"]})
                agent.act()
            elif ev["type"] == "tool_result":
                agent.on_tool_result(ev)
            elif ev["type"] == "end":
                break
        except Exception as e:  # report instead of crashing silently
            log("agent error:", repr(e))
            send({"type": "error", "message": repr(e)[:500], "llm": agent.flush_telemetry() if agent else []})


if __name__ == "__main__":
    main()
