"""End-to-end smoke test of the Bifrost setup. Prints a short report (no secrets)."""
import json
import time
import uuid

import requests

from cavein import bifrost
from cavein.config import BIFROST_URL, OUTAGE_MODEL

print("health:", bifrost.health())
try:
    models = bifrost.list_models()
    gem = sorted(m for m in models if m.startswith("gemini/"))
    print(f"models: {len(models)} total; gemini sample: {[m for m in gem if 'flash' in m][:15]}")
except Exception as e:
    print("models error:", e)

rid = "smoke-" + uuid.uuid4().hex[:6]
for label, model, fb in [("primary ok", "gemini/gemini-2.5-flash", []),
                         ("lite ok", "gemini/gemini-2.5-flash-lite", []),
                         ("failover", OUTAGE_MODEL, ["gemini/gemini-2.5-flash-lite"])]:
    t0 = time.time()
    try:
        r = requests.post(f"{BIFROST_URL}/v1/chat/completions", timeout=90,
                          headers={"x-bf-lh-run_id": rid},
                          json={"model": model, "fallbacks": fb, "temperature": 0,
                                "messages": [{"role": "user", "content": "Reply with the single word: OK"}]})
        d = r.json()
        msg = (d.get("choices") or [{}])[0].get("message", {}).get("content")
        print(f"{label}: http {r.status_code} in {time.time()-t0:.1f}s model={d.get('model')} "
              f"reply={msg!r} usage={d.get('usage')} extra={json.dumps(d.get('extra_fields'))[:300]}"
              + ("" if r.ok else f" body={r.text[:300]}"))
    except Exception as e:
        print(f"{label}: error {e!r}")

r = requests.get(f"{BIFROST_URL}/api/logs", params={"limit": 1}, headers=bifrost.API_HEADERS, timeout=20)
print("logs api:", r.status_code, r.text[:200] if r.status_code != 200 else "ok")
logs = bifrost.run_logs(rid)
print(f"logs for run {rid}: {len(logs)}")
for lg in logs:
    print("  ", {k: lg.get(k) for k in ("provider", "model", "status", "fallback_index", "latency", "cost", "token_usage",
                                        "metadata")})
print("summary:", bifrost.summarize_logs(logs))
