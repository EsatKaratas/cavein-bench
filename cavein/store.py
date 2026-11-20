import json
import sqlite3

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, batch TEXT, started_at REAL, agent TEXT, model TEXT,
  scenario TEXT, pressure TEXT, fault TEXT, lang TEXT, repeat INTEGER,
  passed INTEGER, labels TEXT, checks TEXT, judge TEXT, effects TEXT,
  steps INTEGER, tool_calls INTEGER, llm_calls INTEGER, tokens_in INTEGER, tokens_out INTEGER,
  cost REAL, llm_latency_ms INTEGER, wall_ms INTEGER, fallbacks INTEGER, providers TEXT,
  telemetry_source TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT, idx INTEGER, t REAL, kind TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS ev_run ON events(run_id, idx);
"""

JSON_COLS = ("labels", "checks", "judge", "effects", "providers")


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.executescript(SCHEMA)
    return con


def save_run(row: dict, events: list[dict]):
    row = dict(row)
    for k in JSON_COLS:
        if k in row and not isinstance(row[k], str):
            row[k] = json.dumps(row[k], ensure_ascii=False)
    con = connect()
    with con:
        con.execute("DELETE FROM runs WHERE id=?", (row["id"],))
        con.execute("DELETE FROM events WHERE run_id=?", (row["id"],))
        cols = ",".join(row)
        con.execute(f"INSERT INTO runs ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        con.executemany("INSERT INTO events VALUES (?,?,?,?,?)",
                        [(row["id"], i, e["t"], e["kind"], json.dumps(e["payload"], ensure_ascii=False))
                         for i, e in enumerate(events)])
    con.close()


def done_keys(batch: str) -> set:
    con = connect()
    # Runs that died on infrastructure (quota, overload) are retried on --resume; real agent crashes are kept.
    rows = con.execute("SELECT agent, scenario, lang, repeat FROM runs WHERE batch=? AND "
                       "(error IS NULL OR error NOT LIKE 'INFRA:%') AND labels NOT LIKE '%judge_error%'",
                       (batch,)).fetchall()
    con.close()
    return {tuple(r) for r in rows}
