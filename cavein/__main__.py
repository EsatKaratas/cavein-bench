"""CLI.

  python -m cavein check                         # is Bifrost up? which models can we use?
  python -m cavein run --agents naive-scout --scenarios legit_refund --langs en --repeats 1
  python -m cavein run --agents all --langs en,tr,ar --repeats 2 --batch main
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import bifrost
from .runner import load_agents, load_scenarios, run_one
from .store import done_keys


def cmd_check(_):
    ok = bifrost.health()
    print(f"Bifrost reachable: {ok}")
    if not ok:
        sys.exit(1)
    try:
        models = bifrost.list_models()
        print(f"{len(models)} models available. Some of them:")
        for m in sorted(models)[:400]:
            print("  ", m)
    except Exception as e:
        print("could not list models:", e)


def cmd_run(a):
    agents = load_agents()
    scenarios = load_scenarios()
    names = list(agents) if a.agents == "all" else a.agents.split(",")
    scen = list(scenarios) if a.scenarios == "all" else a.scenarios.split(",")
    langs = a.langs.split(",")
    for n in names:
        if n not in agents:
            sys.exit(f"unknown agent {n}. Known: {list(agents)}")
    for s in scen:
        if s not in scenarios:
            sys.exit(f"unknown scenario {s}. Known: {list(scenarios)}")

    done = done_keys(a.batch) if a.resume else set()
    jobs = [(n, s, l, r) for r in range(1, a.repeats + 1) for n in names for s in scen for l in langs
            if (n, s, l, r) not in done]
    print(f"batch={a.batch}: {len(jobs)} runs to do ({len(done)} already done)")

    def work(job):
        n, s, l, r = job
        if a.pause:
            time.sleep(a.pause)
        return run_one(n, agents[n], scenarios[s], l, r, a.batch, use_judge=not a.no_judge)

    passed = total = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(work, j): j for j in jobs}
        for f in as_completed(futs):
            n, s, l, r = futs[f]
            try:
                row = f.result()
            except Exception as e:  # harness bug: report and keep going
                print(f"  HARNESS ERROR {n} {s} {l} r{r}: {e!r}")
                continue
            total += 1
            passed += row["passed"]
            mark = "PASS" if row["passed"] else "FAIL"
            print(f"  {mark} {n:24s} {s:18s} {l} r{r}  labels={row['labels']}  "
                  f"llm={row['llm_calls']} tok={row['tokens_in']}+{row['tokens_out']} fb={row['fallbacks']} "
                  f"src={row['telemetry_source']} {row['error'][:80]}")
    print(f"done: {passed}/{total} passed")


def main():
    p = argparse.ArgumentParser(prog="cavein")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    r = sub.add_parser("run")
    r.add_argument("--agents", default="all")
    r.add_argument("--scenarios", default="all")
    r.add_argument("--langs", default="en,tr,ar")
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--batch", default=time.strftime("b%m%d-%H%M"))
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--pause", type=float, default=0.0, help="seconds to wait before each run (rate limits)")
    r.add_argument("--no-judge", action="store_true")
    r.add_argument("--resume", action="store_true", help="skip runs already finished in this batch")
    r.set_defaults(fn=cmd_run)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
