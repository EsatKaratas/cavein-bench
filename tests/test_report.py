"""scripts/report.py edge cases: no database, and a database with no LLM calls (rule-based agent only)."""
import json
import os
import sqlite3
import subprocess
import sys

from cavein.config import ROOT
from cavein.store import SCHEMA


def run_report(tmp_path, db, *args):
    env = dict(os.environ, CAVEIN_DB=str(db), CAVEIN_RESULTS=str(tmp_path / "results"), PYTHONIOENCODING="utf-8",
               MPLCONFIGDIR=str(tmp_path / "mpl"))
    return subprocess.run([sys.executable, "-m", "scripts.report", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


def test_missing_database_gives_clear_message(tmp_path):
    r = run_report(tmp_path, tmp_path / "nope" / "cavein.db")
    assert r.returncode == 1
    assert "No results database" in r.stderr
    assert "Traceback" not in r.stderr


def test_rule_based_only_database_without_token_counts(tmp_path):
    db = tmp_path / "cavein.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    for i, (scenario, lang, passed) in enumerate([("legit_refund", "en", 1), ("fake_manager", "tr", 0),
                                                  ("fake_manager", "ar", 0)]):
        # No LLM calls: token/cost columns left NULL, as a scripted agent without Bifrost telemetry can leave them.
        con.execute("INSERT INTO runs (id, batch, agent, model, scenario, fault, lang, repeat, passed, labels, "
                    "tool_calls, wall_ms, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"r{i}", "selftest", "pushover-scripted", "none/rule-based", scenario, "none", lang, 1, passed,
                     json.dumps([] if passed else ["caved_refund"]), 3, 10, ""))
    con.commit()
    con.close()

    r = run_report(tmp_path, db)  # default batches main+hard: none in this db
    assert r.returncode == 1 and "selftest" in r.stderr and "Traceback" not in r.stderr

    r = run_report(tmp_path, db, "selftest")
    assert r.returncode == 0, r.stderr
    md = (tmp_path / "results" / "RESULTS.md").read_text(encoding="utf-8")
    assert "insufficient data" in md  # 3 scored runs < MIN_RUNS: listed, not ranked
    assert "3/6 scored" in md  # 2 scenarios x 3 languages expected, 3 combinations scored
