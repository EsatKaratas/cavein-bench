"""Builds results/RESULTS.md and PNG charts from the SQLite results.

    python -m scripts.report [batch]
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(os.environ.get("TMPDIR", "/tmp")) / "mpl"))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from cavein.config import DB_PATH, ROOT  # noqa: E402
from cavein.scoring import LABEL_INFO  # noqa: E402

BATCHES = sys.argv[1].split(",") if len(sys.argv) > 1 else ["main", "hard"]
BATCH = "+".join(BATCHES)
DB = Path(os.environ.get("CAVEIN_DB", DB_PATH))
OUT = Path(os.environ.get("CAVEIN_RESULTS", ROOT / "results"))
LANG = {"en": "English", "tr": "Turkish", "ar": "Arabic"}
COLORS = {"en": "#4C78A8", "tr": "#E45756", "ar": "#54A24B"}
MIN_RUNS = 10  # agents with fewer scored runs are listed, but not ranked

if not DB.exists():
    sys.exit(f"No results database at {DB}. Run a benchmark first, e.g. "
             "`python -m cavein run --agents pushover-scripted --no-judge --batch selftest`, "
             "then `python -m scripts.report selftest`.")
con = sqlite3.connect(DB)
raw = pd.read_sql(f"SELECT * FROM runs WHERE batch IN ({','.join('?'*len(BATCHES))})", con, params=BATCHES)
batches = [b for (b,) in con.execute("SELECT DISTINCT batch FROM runs")] if raw.empty else []
con.close()
if raw.empty:
    sys.exit(f"No runs for batch {BATCH} in {DB}. Batches in this database: {', '.join(batches) or 'none'}. "
             "Pass one as an argument, e.g. `python -m scripts.report selftest`.")
raw["labels"] = raw["labels"].apply(json.loads)
infra = raw["labels"].apply(lambda x: "infra_error" in x or "judge_error" in x).astype(bool)
excluded = int(infra.sum())
df = raw[~infra].copy()
if df.empty:
    sys.exit(f"All {excluded} runs for batch {BATCH} failed on infrastructure (provider or judge); nothing to score.")
for c in ("tokens_in", "tokens_out", "llm_calls", "tool_calls", "cost", "wall_ms", "fallbacks"):
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0) if c in df else 0
df["tokens"] = df["tokens_in"] + df["tokens_out"]
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({"figure.dpi": 140, "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

# 1. leaderboard
lb = df.groupby("agent").agg(model=("model", "first"), runs=("id", "count"), pass_rate=("passed", "mean"),
                             tokens=("tokens", "mean"), llm_calls=("llm_calls", "mean"),
                             tool_calls=("tool_calls", "mean"), seconds=("wall_ms", lambda s: s.mean() / 1000),
                             cost=("cost", "mean"), failovers=("fallbacks", "sum"))
# Ranked agents first (enough scored runs), then agents with too little data, each by pass rate.
lb["enough"] = lb["runs"] >= MIN_RUNS
lb = lb.sort_values(["enough", "pass_rate"], ascending=False)
fig, ax = plt.subplots(figsize=(7, 3.2))
rev = lb[::-1]
ax.barh([f"{a} (n={n})" for a, n in zip(rev.index, rev["runs"])], rev["pass_rate"],
        color=["#4C78A8" if e else "#BBBBBB" for e in rev["enough"]])
for i, (v, e) in enumerate(zip(rev["pass_rate"], rev["enough"])):
    ax.text(v + 0.01, i, f"{v:.0%}" + ("" if e else "  insufficient data"), va="center")
ax.set_xlim(0, 1.1 if lb["enough"].all() else 1.45)
ax.set_xlabel(f"pass rate ({df['scenario'].nunique()} scenarios × EN/TR/AR; grey = fewer than {MIN_RUNS} scored runs)")
ax.set_title("CaveIn leaderboard")
fig.tight_layout()
fig.savefig(OUT / "leaderboard.png")

# 2. heatmap agent x scenario
heat = df.pivot_table(index="agent", columns="scenario", values="passed", aggfunc="mean").reindex(lb.index)
fig, ax = plt.subplots(figsize=(9, 3.4))
im = ax.imshow(heat.values, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
ax.set_xticks(range(len(heat.columns)), heat.columns, rotation=30, ha="right")
ax.set_yticks(range(len(heat.index)), heat.index)
for i in range(heat.shape[0]):
    for j in range(heat.shape[1]):
        v = heat.values[i, j]
        if v == v:
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=8)
ax.set_title("Where agents cave: pass rate by scenario")
fig.tight_layout()
fig.savefig(OUT / "heatmap.png")

# 3. language gap
lg = df.pivot_table(index="agent", columns="lang", values="passed", aggfunc="mean").reindex(lb.index)
fig, ax = plt.subplots(figsize=(8, 3.2))
w = 0.27
for k, l in enumerate(["en", "tr", "ar"]):
    if l in lg:
        ax.bar([x + (k - 1) * w for x in range(len(lg))], lg[l], w, label=LANG[l], color=COLORS[l])
ax.set_xticks(range(len(lg)), lg.index, rotation=15)
ax.set_ylim(0, 1.05)
ax.set_ylabel("pass rate")
ax.legend(frameon=False, ncol=3)
ax.set_title("Same agent, same scenarios, different customer language")
fig.tight_layout()
fig.savefig(OUT / "language_gap.png")

# 4. failure types
lab = df.explode("labels").dropna(subset=["labels"])
fail_tab = lab.groupby(["agent", "labels"]).size().unstack(fill_value=0) if not lab.empty else pd.DataFrame()
if not fail_tab.empty:
    fig, ax = plt.subplots(figsize=(8, 3.2))
    fail_tab.reindex(lb.index).fillna(0).plot.barh(stacked=True, ax=ax, colormap="tab10")
    ax.invert_yaxis()
    ax.set_xlabel("failed runs (a run can fail in several ways)")
    ax.legend(frameon=False, fontsize=8, bbox_to_anchor=(1, 1))
    ax.set_title("How agents fail")
    fig.tight_layout()
    fig.savefig(OUT / "failures.png")

# markdown
def pct(x):
    return f"{x:.0%}" if x == x else "–"


md = [f"# CaveIn results — batch `{BATCH}`", "",
      f"{len(df)} scored runs ({excluded} runs excluded because the provider failed after every Bifrost "
      f"fallback or the judge could not answer; they are infrastructure noise, not agent behaviour).", "",
      "## Leaderboard", "",
      (f"Best agent: **`{lb.index[0]}`** ({pct(lb['pass_rate'].iloc[0])} over {lb['runs'].iloc[0]} scored runs). "
       if lb["enough"].iloc[0] else f"No agent has {MIN_RUNS}+ scored runs yet, so no agent is ranked. ")
      + f"Agents with fewer than {MIN_RUNS} scored runs are marked *insufficient data* and listed last.", "",
      "| Agent | Model | Scored runs | Pass rate | EN | TR | AR | Tokens/run | LLM calls/run | Tool calls/run | "
      "Sec/run | Failovers |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for a, r in lb.iterrows():
    md.append(f"| `{a}` | `{r['model']}` | {r['runs']}{'' if r['enough'] else ' (insufficient data)'} | "
              f"**{pct(r['pass_rate'])}** | {pct(lg.loc[a].get('en', float('nan')))} | "
              f"{pct(lg.loc[a].get('tr', float('nan')))} | {pct(lg.loc[a].get('ar', float('nan')))} | "
              f"{r['tokens']:,.0f} | {r['llm_calls']:.1f} | {r['tool_calls']:.1f} | {r['seconds']:.1f} | "
              f"{int(r['failovers'])} |")
md += ["", "![leaderboard](leaderboard.png)", "", "## Pass rate by scenario", "",
       "| Agent | " + " | ".join(heat.columns) + " |", "|---|" + "---|" * len(heat.columns)]
for a, r in heat.iterrows():
    md.append(f"| `{a}` | " + " | ".join(pct(v) for v in r.values) + " |")
md += ["", "![heatmap](heatmap.png)", "", "## Coverage: combinations without a scored run", "",
       f"Expected: every agent × every scenario × EN/TR/AR seen in batch `{BATCH}`. "
       "Missing = never run yet, or every attempt failed on infrastructure. Empty cells above are these.", ""]
done = set(zip(df["agent"], df["scenario"], df["lang"]))
for a in sorted(raw["agent"].unique()):
    miss = {}
    for s in sorted(raw["scenario"].unique()):
        langs = [l for l in LANG if (a, s, l) not in done]
        if langs:
            miss[s] = langs
    total = raw["scenario"].nunique() * len(LANG)
    n_miss = sum(len(v) for v in miss.values())
    md.append(f"- `{a}`: {total - n_miss}/{total} scored"
              + (": missing " + "; ".join(f"{s} ({', '.join(v)})" for s, v in miss.items()) if miss else " ✓"))
md += ["", "## Language gap", "", "![language gap](language_gap.png)", ""]
if not fail_tab.empty:
    md += ["## Failure types", "", "| Agent | " + " | ".join(fail_tab.columns) + " |",
           "|---|" + "---|" * len(fail_tab.columns)]
    for a, r in fail_tab.iterrows():
        md.append(f"| `{a}` | " + " | ".join(str(int(v)) for v in r.values) + " |")
    md += ["", "Legend: " + "; ".join(f"`{k}` = {v}" for k, v in LABEL_INFO.items() if k in fail_tab.columns),
           "", "![failures](failures.png)", ""]

# notable caved runs
caved = df[df["labels"].apply(lambda x: any(l in x for l in ("caved_refund", "data_leak", "excess_discount",
                                                              "false_claim", "verbal_promise")))]
if not caved.empty:
    md += ["## Examples of caving (open them in the dashboard's Run explorer)", ""]
    for _, r in caved.head(12).iterrows():
        md.append(f"- `{r['agent']}` · `{r['scenario']}` · {LANG[r['lang']]}: {', '.join(r['labels'])}")
(OUT / "RESULTS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("\n".join(md[:40]))
print(f"\nwrote {OUT}/RESULTS.md and charts")
