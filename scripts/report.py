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
OUT = ROOT / "results"
OUT.mkdir(exist_ok=True)
LANG = {"en": "English", "tr": "Turkish", "ar": "Arabic"}
COLORS = {"en": "#4C78A8", "tr": "#E45756", "ar": "#54A24B"}

con = sqlite3.connect(DB_PATH)
df = pd.read_sql(f"SELECT * FROM runs WHERE batch IN ({','.join('?'*len(BATCHES))})", con, params=BATCHES)
con.close()
df["labels"] = df["labels"].apply(json.loads)
infra = df["labels"].apply(lambda x: "infra_error" in x or "judge_error" in x)
excluded = int(infra.sum())
df = df[~infra].copy()
df["tokens"] = df["tokens_in"] + df["tokens_out"]

plt.rcParams.update({"figure.dpi": 140, "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

# 1. leaderboard
lb = df.groupby("agent").agg(model=("model", "first"), runs=("id", "count"), pass_rate=("passed", "mean"),
                             tokens=("tokens", "mean"), llm_calls=("llm_calls", "mean"),
                             tool_calls=("tool_calls", "mean"), seconds=("wall_ms", lambda s: s.mean() / 1000),
                             cost=("cost", "mean"), failovers=("fallbacks", "sum")).sort_values("pass_rate",
                                                                                                ascending=False)
fig, ax = plt.subplots(figsize=(7, 3.2))
ax.barh(lb.index[::-1], lb["pass_rate"][::-1], color="#4C78A8")
for i, v in enumerate(lb["pass_rate"][::-1]):
    ax.text(v + 0.01, i, f"{v:.0%}", va="center")
ax.set_xlim(0, 1.1)
ax.set_xlabel(f"pass rate ({df['scenario'].nunique()} scenarios × EN/TR/AR)")
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
      "| Agent | Model | Pass rate | EN | TR | AR | Tokens/run | LLM calls/run | Tool calls/run | Sec/run | Failovers |",
      "|---|---|---|---|---|---|---|---|---|---|---|"]
for a, r in lb.iterrows():
    md.append(f"| `{a}` | `{r['model']}` | **{pct(r['pass_rate'])}** | {pct(lg.loc[a].get('en', float('nan')))} | "
              f"{pct(lg.loc[a].get('tr', float('nan')))} | {pct(lg.loc[a].get('ar', float('nan')))} | "
              f"{r['tokens']:,.0f} | {r['llm_calls']:.1f} | {r['tool_calls']:.1f} | {r['seconds']:.1f} | "
              f"{int(r['failovers'])} |")
md += ["", "![leaderboard](leaderboard.png)", "", "## Pass rate by scenario", "",
       "| Agent | " + " | ".join(heat.columns) + " |", "|---|" + "---|" * len(heat.columns)]
for a, r in heat.iterrows():
    md.append(f"| `{a}` | " + " | ".join(pct(v) for v in r.values) + " |")
md += ["", "![heatmap](heatmap.png)", "", "## Language gap", "", "![language gap](language_gap.png)", ""]
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
