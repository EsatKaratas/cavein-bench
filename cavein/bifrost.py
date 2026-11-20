"""Small client for the Bifrost gateway: chat calls (for the judge) and the logs API.

The logs API is the benchmark's source of truth for tokens, cost, latency and
fallbacks: Bifrost sits outside every agent, so it sees every model call no matter
what the agent is written in. Agents tag their calls with `x-bf-lh-run_id`, which
Bifrost stores as log metadata, so we can pull exactly the calls of one run.
"""
import os
import time

import requests

from .config import BIFROST_URL

# Bifrost locks /api until an admin account exists; the setup token opens it for local use.
API_HEADERS = {"X-Bifrost-Setup-Token": os.getenv("BIFROST_SETUP_TOKEN", "")}


def chat(model, messages, fallbacks=(), temperature=0.0, headers=None, timeout=120):
    body = {"model": model, "messages": messages, "temperature": temperature, "fallbacks": list(fallbacks)}
    r = requests.post(f"{BIFROST_URL}/v1/chat/completions", json=body, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def health():
    try:
        r = requests.get(f"{BIFROST_URL}/health", timeout=5)
        return r.status_code < 500
    except requests.RequestException:
        return False


def list_models():
    r = requests.get(f"{BIFROST_URL}/v1/models", timeout=20)
    r.raise_for_status()
    return [m.get("id") for m in r.json().get("data", [])]


def run_logs(run_id, wait_s=2.0, attempts=4):
    """Fetch all Bifrost log entries tagged with this run id (logging is async, so retry briefly)."""
    logs = []
    for i in range(attempts):
        time.sleep(wait_s if i else wait_s / 2)
        try:
            r = requests.get(f"{BIFROST_URL}/api/logs",
                             params={"metadata_run_id": run_id, "limit": 200, "sort_by": "timestamp", "order": "asc"},
                             headers=API_HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            logs = r.json().get("logs") or []
        except requests.RequestException:
            continue
        if logs:
            break
    return logs


def summarize_logs(logs):
    """Turn Bifrost log entries into per-run metrics."""
    out = {"llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "llm_latency_ms": 0,
           "fallbacks": 0, "providers": {}, "errors": 0}
    for lg in logs:
        out["llm_calls"] += 1
        tu = lg.get("token_usage") or {}
        out["tokens_in"] += int(tu.get("prompt_tokens") or 0)
        out["tokens_out"] += int(tu.get("completion_tokens") or 0)
        out["cost"] += float(lg.get("cost") or 0)
        out["llm_latency_ms"] += int(float(lg.get("latency") or 0))
        if (lg.get("fallback_index") or 0) > 0:
            out["fallbacks"] += 1
        if lg.get("status") not in (None, "success", "processing"):
            out["errors"] += 1
        key = f"{lg.get('provider')}/{lg.get('model')}"
        out["providers"][key] = out["providers"].get(key, 0) + 1
    return out
