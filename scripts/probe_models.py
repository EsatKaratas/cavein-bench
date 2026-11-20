"""Which chat models actually answer through Bifrost with this key? Prints one line per model."""
import json
import sys
import time

import requests

from cavein import bifrost
from cavein.config import BIFROST_URL

models = bifrost.list_models()
print("all gemini models:", sorted(m for m in models if m.startswith("gemini/") and not any(
    w in m for w in ("tts", "audio", "image", "live", "embedding", "veo", "imagen"))))
cands = sys.argv[1:] or ["gemini/gemini-3.8-flash", "gemini/gemini-3.5-flash", "gemini/gemini-3.5-flash-lite",
                         "gemini/gemini-3-flash-preview", "gemini/gemini-3.1-flash-lite",
                         "gemini/gemini-3.1-flash-lite-preview", "gemini/gemini-3.1-pro-preview"]
tool = [{"type": "function", "function": {"name": "ping", "description": "ping",
                                           "parameters": {"type": "object", "properties": {}, "required": []}}}]
for m in cands:
    t0 = time.time()
    try:
        r = requests.post(f"{BIFROST_URL}/v1/chat/completions", timeout=60, json={
            "model": m, "temperature": 0, "tools": tool,
            "messages": [{"role": "user", "content": "Reply with the single word OK."}]})
        d = r.json()
        if r.ok:
            print(f"OK   {m:45s} {time.time()-t0:4.1f}s usage={d.get('usage')} reply={d['choices'][0]['message'].get('content')!r}")
        else:
            print(f"FAIL {m:45s} {r.status_code} {json.dumps(d.get("error"))[:1500]}")
    except Exception as e:
        print(f"ERR  {m:45s} {e!r}"[:200])
