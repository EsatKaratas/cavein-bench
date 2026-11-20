"""search_web tool backed by Tavily, with a response cache.

The cache makes runs repeatable: the same query returns the same results in every
repeat and for every agent, and the benchmark can be re-run without a Tavily key.
"""
import json
import threading

import requests

from .config import TAVILY_API_KEY, TAVILY_CACHE

_lock = threading.Lock()


def _load():
    if TAVILY_CACHE.exists():
        return json.loads(TAVILY_CACHE.read_text(encoding="utf-8"))
    return {}


def tavily_search(query: str) -> dict:
    key = " ".join(query.lower().split())
    with _lock:
        cache = _load()
        if key in cache:
            return cache[key]
    if not TAVILY_API_KEY:
        return {"results": [], "note": "web search unavailable (no TAVILY_API_KEY and no cached result)"}
    try:
        r = requests.post("https://api.tavily.com/search", timeout=30,
                          headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                          json={"query": query, "max_results": 4, "search_depth": "basic",
                                "topic": "news", "time_range": "month", "include_answer": False})
        r.raise_for_status()
        data = r.json()
        result = {"results": [{"title": x.get("title"), "url": x.get("url"),
                               "snippet": (x.get("content") or "")[:400], "published": x.get("published_date")}
                              for x in data.get("results", [])]}
    except requests.RequestException as e:
        return {"error": f"search failed: {e.__class__.__name__}"}
    with _lock:
        cache = _load()
        cache[key] = result
        TAVILY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TAVILY_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
