"""CaveIn dashboard:  streamlit run app.py"""
import json
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from cavein.config import DB_PATH
from cavein.scoring import LABEL_INFO

st.set_page_config(page_title="CaveIn", page_icon="🧱", layout="wide")
# Arabic transcripts: each paragraph takes its direction (RTL/LTR) from its own first letter.
st.markdown("<style>[data-testid='stMarkdownContainer'] :is(p, li) {unicode-bidi: plaintext; text-align: start}"
            "</style>", unsafe_allow_html=True)

LANG = {"en": "English", "tr": "Turkish", "ar": "Arabic"}
PRESSURE = {"none": "Control / fault only", "anger": "Anger", "authority": "Fake authority",
            "social_engineering": "Social engineering", "misinformation": "Misinformation",
            "emotional": "Emotional plea", "bargaining": "Bargaining"}


@st.cache_data(ttl=10)
def load():
    con = sqlite3.connect(DB_PATH)
    runs = pd.read_sql("SELECT * FROM runs", con)
    con.close()
    for c in ("labels", "checks", "judge", "effects", "providers"):
        runs[c] = runs[c].apply(lambda s: json.loads(s) if isinstance(s, str) and s else ({} if c != "labels" else []))
    runs["tokens"] = runs["tokens_in"] + runs["tokens_out"]
    return runs


def events_for(run_id):
    con = sqlite3.connect(DB_PATH)
    ev = con.execute("SELECT idx, t, kind, payload FROM events WHERE run_id=? ORDER BY idx", (run_id,)).fetchall()
    con.close()
    return [{"idx": i, "t": t, "kind": k, "payload": json.loads(p)} for i, t, k, p in ev]


if not DB_PATH.exists():
    st.info("No results yet. Run `python -m cavein run ...` first.")
    st.stop()

runs = load()
st.title("🧱 CaveIn")
st.caption("Does your support agent cave in under pressure? Same tasks for every agent, any framework, "
           "measured through the Bifrost gateway.")

with st.sidebar:
    batches = sorted(runs["batch"].unique(), reverse=True)
    default = [b for b in batches if b in ("main", "hard")] or [b for b in batches if b != "selftest"][:1] \
        or batches[:1]
    sel_batches = st.multiselect("Batch", batches, default=default)
    df = runs[runs["batch"].isin(sel_batches)]
    agents = sorted(df["agent"].unique())
    sel_agents = st.multiselect("Agents", agents, default=agents)
    sel_langs = st.multiselect("Languages", list(LANG), default=list(LANG), format_func=LANG.get)
    df = df[df["agent"].isin(sel_agents) & df["lang"].isin(sel_langs)]
    # Provider outages after all Bifrost fallbacks are not the agent's fault: shown, not scored.
    infra = df["labels"].apply(lambda x: "infra_error" in x or "judge_error" in x)
    st.metric("Scored runs", int((~infra).sum()))
    st.caption(f"{int(infra.sum())} runs excluded: provider or LLM judge failed after every Bifrost fallback "
               "(quota/overload).")
    df = df[~infra]
    if st.button("Reload"):
        st.cache_data.clear()
        st.rerun()

if df.empty:
    st.warning("Nothing selected.")
    st.stop()


def pass_hat_k(d):
    g = d.groupby(["agent", "scenario", "lang"])["passed"].agg(["min", "count"])
    g = g[g["count"] >= 2]
    return g.groupby("agent")["min"].mean() if not g.empty else pd.Series(dtype=float)


tab_lb, tab_cave, tab_lang, tab_fault, tab_run, tab_diff = st.tabs(
    ["🏆 Leaderboard", "🧱 Where they cave", "🌍 Language gap", "⚡ Faults", "🔎 Run explorer", "↔️ Run diff"])

with tab_lb:
    agg = df.groupby("agent").agg(
        model=("model", "first"), runs=("id", "count"), pass_rate=("passed", "mean"),
        crash_rate=("error", lambda s: (s != "").mean()), tokens_per_run=("tokens", "mean"),
        llm_calls_per_run=("llm_calls", "mean"), tool_calls_per_run=("tool_calls", "mean"),
        cost_per_run=("cost", "mean"), seconds_per_run=("wall_ms", lambda s: s.mean() / 1000),
        fallbacks=("fallbacks", "sum"))
    agg["pass^k (all repeats pass)"] = pass_hat_k(df)
    agg["tokens per pass"] = df.groupby("agent").apply(
        lambda d: d["tokens"].sum() / max(d["passed"].sum(), 1), include_groups=False)
    agg = agg.sort_values("pass_rate", ascending=False)
    c1, c2, c3 = st.columns(3)
    best = agg.index[0]
    c1.metric("Best agent", best, f"{agg.loc[best, 'pass_rate']:.0%} pass")
    c2.metric("Overall pass rate", f"{df['passed'].mean():.0%}")
    c3.metric("Bifrost failovers observed", int(df["fallbacks"].sum()))
    st.dataframe(agg.style.format({"pass_rate": "{:.0%}", "crash_rate": "{:.0%}", "pass^k (all repeats pass)": "{:.0%}",
                                   "tokens_per_run": "{:,.0f}", "llm_calls_per_run": "{:.1f}",
                                   "tool_calls_per_run": "{:.1f}", "cost_per_run": "${:.5f}",
                                   "seconds_per_run": "{:.1f}", "tokens per pass": "{:,.0f}"}),
                 width="stretch")
    fig = px.scatter(agg.reset_index(), x="tokens_per_run", y="pass_rate", text="agent", size="runs",
                     labels={"tokens_per_run": "Tokens per run (cost)", "pass_rate": "Pass rate"},
                     title="Reliability vs. cost")
    fig.update_traces(textposition="top center")
    fig.update_yaxes(tickformat=".0%", range=[0, 1.05])
    st.plotly_chart(fig, width="stretch")

with tab_cave:
    heat = df.pivot_table(index="agent", columns="scenario", values="passed", aggfunc="mean")
    fig = px.imshow(heat, text_auto=".0%", color_continuous_scale="RdYlGn", zmin=0, zmax=1, aspect="auto",
                    title="Pass rate by scenario")
    st.plotly_chart(fig, width="stretch")
    lab = df.explode("labels").dropna(subset=["labels"])
    if not lab.empty:
        lab["failure"] = lab["labels"].map(lambda x: LABEL_INFO.get(x, x))
        fig = px.histogram(lab, y="agent", color="failure", barmode="stack", title="Failure types (one run can have several)")
        st.plotly_chart(fig, width="stretch")
    else:
        st.success("No failures in this selection.")

with tab_lang:
    lg = df.groupby(["agent", "lang"])["passed"].mean().reset_index()
    lg["language"] = lg["lang"].map(LANG)
    fig = px.bar(lg, x="agent", y="passed", color="language", barmode="group", title="Pass rate by customer language")
    fig.update_yaxes(tickformat=".0%", range=[0, 1.05])
    st.plotly_chart(fig, width="stretch")
    pv = lg.pivot(index="agent", columns="lang", values="passed")
    if "en" in pv:
        gap = pd.DataFrame({f"gap vs English ({LANG[l]})": pv[l] - pv["en"] for l in pv if l != "en"})
        st.markdown("**Language gap** (negative = the agent is weaker than in English)")
        st.dataframe(gap.style.format("{:+.0%}"), width="stretch")
    ls = df[df["labels"].apply(lambda x: "language_switch" in x)]
    st.caption(f"Runs where the agent answered in the wrong language: {len(ls)}")

with tab_fault:
    fd = df[df["fault"] != "none"]
    if fd.empty:
        st.info("No fault scenarios in this selection.")
    else:
        fa = fd.groupby(["agent", "fault"]).agg(runs=("id", "count"), pass_rate=("passed", "mean"),
                                                 crashes=("error", lambda s: (s != "").sum()),
                                                 bifrost_failovers=("fallbacks", "sum")).reset_index()
        st.dataframe(fa.style.format({"pass_rate": "{:.0%}"}), width="stretch")
        st.markdown("**model_unavailable**: the agent's primary model does not exist. Bifrost fails over to the "
                    "fallback model, so the conversation should continue. **tool_error**: the refund API keeps "
                    "returning 503; the agent must not claim success and must escalate.")
        rows = []
        for _, r in fd.iterrows():
            for k, v in (r["providers"] or {}).items():
                rows.append({"agent": r["agent"], "fault": r["fault"], "provider/model": k, "calls": v})
        if rows:
            st.plotly_chart(px.bar(pd.DataFrame(rows), x="agent", y="calls", color="provider/model",
                                   facet_col="fault", title="Which model actually answered (from Bifrost logs)"),
                            width="stretch")

with tab_run:
    c1, c2, c3, c4 = st.columns(4)
    a = c1.selectbox("Agent", sorted(df["agent"].unique()))
    s = c2.selectbox("Scenario", sorted(df["scenario"].unique()))
    l = c3.selectbox("Language", sorted(df[(df.agent == a) & (df.scenario == s)]["lang"].unique()) or ["en"],
                     format_func=LANG.get)
    cand = df[(df.agent == a) & (df.scenario == s) & (df.lang == l)].sort_values("repeat")
    if cand.empty:
        st.info("No run for this combination.")
    else:
        rid = c4.selectbox("Run", cand["id"], format_func=lambda x: f"repeat {x.split('.r')[-1].split('.')[0]}"
                           + (" ✅" if cand.set_index('id').loc[x, 'passed'] else " ❌"))
        r = cand.set_index("id").loc[rid]
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Result", "PASS" if r["passed"] else "FAIL")
        m2.metric("LLM calls", int(r["llm_calls"]))
        m3.metric("Tool calls", int(r["tool_calls"]))
        m4.metric("Tokens", f"{int(r['tokens']):,}")
        m5.metric("Failovers", int(r["fallbacks"]))
        m6.metric("Seconds", f"{r['wall_ms'] / 1000:.1f}")
        if r["labels"]:
            st.error(" · ".join(LABEL_INFO.get(x, x) for x in r["labels"]))
        left, right = st.columns([3, 2])
        with right:
            st.markdown("**Checks**")
            st.dataframe(pd.DataFrame(r["checks"])[["name", "passed", "detail"]], hide_index=True,
                         width="stretch")
            st.markdown("**Judge**")
            st.json(r["judge"], expanded=True)
            st.markdown("**Effects on the back office**")
            st.json(r["effects"], expanded=False)
        with left:
            st.markdown("**Timeline**")
            evs = events_for(rid)
            t0 = evs[0]["t"] if evs else 0
            for e in evs:
                p, k, ts = e["payload"], e["kind"], f"+{e['t'] - t0:5.1f}s"
                llm = p.get("llm") or []
                chip = " ".join(f"`{x.get('model')} · {x.get('tokens_in', 0)}+{x.get('tokens_out', 0)} tok · "
                                f"{x.get('latency_ms', 0)} ms{' · ' + x['purpose'] if x.get('purpose') else ''}`"
                                for x in llm)
                if k == "customer":
                    with st.chat_message("user"):
                        st.markdown(f"{p['content']}  \n<small>{ts}</small>", unsafe_allow_html=True)
                elif k == "agent_reply":
                    with st.chat_message("assistant"):
                        st.markdown(p["content"] or "_(empty reply)_")
                        if chip:
                            st.caption(f"{ts} · LLM: {chip}")
                elif k == "tool_call":
                    st.markdown(f"🔧 **{p['name']}** `{json.dumps(p['arguments'], ensure_ascii=False)}`  {ts}"
                                + (f"  \nLLM: {chip}" if chip else ""))
                elif k == "tool_result":
                    if p.get("injected_fault"):
                        st.warning(f"⚡ injected fault → {p['content']}")
                    else:
                        st.code(p["content"][:600], language="json")
                elif k == "guard_note":
                    st.info(f"🛡️ {p['content']}" + (f"  \nLLM: {chip}" if chip else ""))
                elif k == "crash":
                    st.error(f"💥 {p['reason']}")
                elif k == "bifrost_logs":
                    with st.expander(f"Bifrost log entries for this run ({p['count']})"):
                        st.dataframe(pd.DataFrame(p["entries"]), width="stretch")


def signature(evs):
    sig = []
    for e in evs:
        if e["kind"] == "tool_call":
            sig.append(("tool", e["payload"]["name"], json.dumps(e["payload"]["arguments"], sort_keys=True,
                                                                ensure_ascii=False)))
        elif e["kind"] == "agent_reply":
            sig.append(("reply",))
        elif e["kind"] == "customer":
            sig.append(("customer", e["payload"]["content"]))
        elif e["kind"] == "guard_note":
            sig.append(("guard",))
    return sig


with tab_diff:
    st.markdown("Pick two runs of the same scenario (ideally one ✅ and one ❌). The first step where they "
                "take a different action is highlighted: that is usually where the agent caved.")
    c1, c2, c3 = st.columns(3)
    s = c1.selectbox("Scenario ", sorted(df["scenario"].unique()))
    l = c2.selectbox("Language ", sorted(df[df.scenario == s]["lang"].unique()), format_func=LANG.get)
    pool = df[(df.scenario == s) & (df.lang == l)].sort_values(["passed", "agent"])
    label = lambda x: f"{'✅' if pool.set_index('id').loc[x, 'passed'] else '❌'} {pool.set_index('id').loc[x, 'agent']} " \
                      f"r{pool.set_index('id').loc[x, 'repeat']}"
    if len(pool) < 2:
        st.info("Need at least two runs.")
    else:
        ids = list(pool["id"])
        fails = [i for i in ids if not pool.set_index("id").loc[i, "passed"]]
        oks = [i for i in ids if pool.set_index("id").loc[i, "passed"]]
        ra = c3.selectbox("Run A", ids, index=ids.index(oks[0]) if oks else 0, format_func=label)
        rb = st.selectbox("Run B", ids, index=ids.index(fails[0]) if fails else min(1, len(ids) - 1), format_func=label)
        ea, eb = events_for(ra), events_for(rb)
        sa, sb = signature(ea), signature(eb)
        div = next((i for i in range(min(len(sa), len(sb))) if sa[i] != sb[i]), None)
        if div is None and len(sa) != len(sb):
            div = min(len(sa), len(sb))
        if div is None:
            st.success("Both runs took exactly the same actions.")
        else:
            st.error(f"First divergence at step {div + 1}: A → {sa[div] if div < len(sa) else '(ended)'} | "
                     f"B → {sb[div] if div < len(sb) else '(ended)'}")
        ca, cb = st.columns(2)
        for col, evs, sig in ((ca, ea, sa), (cb, eb, sb)):
            with col:
                step = -1
                for e in evs:
                    k, p = e["kind"], e["payload"]
                    if k not in ("customer", "tool_call", "agent_reply", "guard_note", "tool_result"):
                        continue
                    if k != "tool_result":
                        step += 1
                    mark = "🟥 " if div is not None and step == div and k != "tool_result" else ""
                    if k == "customer":
                        st.markdown(f"{mark}**Customer:** {p['content']}")
                    elif k == "agent_reply":
                        st.markdown(f"{mark}**Agent:** {p['content']}")
                    elif k == "tool_call":
                        st.markdown(f"{mark}🔧 `{p['name']}({json.dumps(p['arguments'], ensure_ascii=False)})`")
                    elif k == "guard_note":
                        st.markdown(f"{mark}🛡️ {p['content']}")
                    elif k == "tool_result":
                        st.caption(p["content"][:200])
