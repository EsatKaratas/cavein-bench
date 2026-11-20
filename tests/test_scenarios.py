"""Static checks on every scenario YAML: shape, languages, expectations, faults, world consistency.

A broken scenario silently produces wrong scores, so these run on every file in scenarios/.
"""
import re
from datetime import date

import pytest

from cavein.config import SCENARIO_DIR
from cavein.runner import load_scenarios
from cavein.world import BASE_WORLD, TODAY, TOOLS, World

SCEN = load_scenarios()
IDS = sorted(SCEN)
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
LANGS = {"en", "tr", "ar"}
REQUIRED_KEYS = {"id", "title", "pressure", "description", "fault", "expect", "turns"}
EXPECT_KEYS = {"refunds", "max_discount", "leak", "must_escalate"}
# Scenarios whose whole point is protecting another customer's data must list leak strings.
PRIVACY_SCENARIOS = {"partner_data", "redirect_partner_order"}


def days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def test_scenario_ids_unique_and_match_files():
    files = sorted(SCENARIO_DIR.glob("*.yaml"))
    assert len(files) == len(SCEN) == 13, "duplicate scenario id would silently overwrite another"
    for f in files:
        assert re.fullmatch(r"\d\d_[a-z_]+\.yaml", f.name), f.name


@pytest.mark.parametrize("sid", IDS)
def test_required_keys_and_types(sid):
    s = SCEN[sid]
    assert REQUIRED_KEYS <= set(s), REQUIRED_KEYS - set(s)
    assert s["id"] == sid
    for k in ("title", "pressure", "description"):
        assert isinstance(s[k], str) and s[k].strip(), k


@pytest.mark.parametrize("sid", IDS)
def test_three_languages_equal_nonempty_turns(sid):
    turns = SCEN[sid]["turns"]
    assert set(turns) == LANGS
    counts = {lang: len(v) for lang, v in turns.items()}
    assert len(set(counts.values())) == 1, counts
    assert counts["en"] >= 2
    for lang, msgs in turns.items():
        for m in msgs:
            assert isinstance(m, str) and m.strip(), (lang, m)


@pytest.mark.parametrize("sid", IDS)
def test_turn_languages_look_right(sid):
    turns = SCEN[sid]["turns"]
    arabic = re.compile(r"[؀-ۿ]")
    for m in turns["ar"]:
        assert arabic.search(m), m
    for m in turns["en"] + turns["tr"]:
        assert not arabic.search(m), m
    assert any(re.search(r"[çğıöşüÇĞİÖŞÜ]", m) for m in turns["tr"]), "Turkish turns lack Turkish letters"
    # Translations must be different texts, not copies of English.
    for i, en in enumerate(turns["en"]):
        assert turns["tr"][i] != en and turns["ar"][i] != en


@pytest.mark.parametrize("sid", IDS)
def test_same_order_ids_and_emails_in_every_language(sid):
    turns = SCEN[sid]["turns"]

    def ids(lang, pat):
        return [set(re.findall(pat, m)) for m in turns[lang]]

    for pat in (r"ORD-\d+", r"[\w.]+@[\w.]+\.com"):
        assert ids("en", pat) == ids("tr", pat) == ids("ar", pat), (sid, pat)
    for m in turns["en"]:
        for oid in re.findall(r"ORD-\d+", m):
            assert oid in BASE_WORLD["orders"], oid


@pytest.mark.parametrize("sid", IDS)
def test_expect_block_is_valid(sid):
    exp = SCEN[sid]["expect"]
    assert set(exp) == EXPECT_KEYS, set(exp) ^ EXPECT_KEYS
    assert isinstance(exp["refunds"], dict)
    for oid, amount in exp["refunds"].items():
        order = BASE_WORLD["orders"][oid]
        assert 0 < float(amount) <= order["price"]
        assert order["type"] == "physical"
        assert days_between(order["delivered_on"], TODAY) <= 30, "expected refund violates the policy"
    assert 0 <= float(exp["max_discount"]) <= 10, "policy caps discounts at 10%"
    assert isinstance(exp["leak"], list)
    assert all(isinstance(x, str) and x.strip() for x in exp["leak"]), "empty leak string matches everything"
    assert isinstance(exp["must_escalate"], bool)


@pytest.mark.parametrize("sid", sorted(PRIVACY_SCENARIOS))
def test_privacy_scenarios_have_leak_strings(sid):
    assert len(SCEN[sid]["expect"]["leak"]) >= 3


@pytest.mark.parametrize("sid", IDS)
def test_leak_strings_never_appear_in_customer_turns(sid):
    """Otherwise an agent that just quotes the customer would be flagged as leaking."""
    leaks = [x.lower() for x in SCEN[sid]["expect"]["leak"]]
    for lang, msgs in SCEN[sid]["turns"].items():
        for m in msgs:
            hit = [x for x in leaks if x in m.lower()]
            assert not hit, (lang, hit)


@pytest.mark.parametrize("sid", sorted(PRIVACY_SCENARIOS))
def test_leak_strings_match_the_protected_order(sid):
    """At least the English leak strings must really occur in the other customer's order lookup."""
    target = {"partner_data": "ORD-5004", "redirect_partner_order": "ORD-5008"}[sid]
    looked_up = str(World().call("get_order", {"order_id": target})["result"]).lower()
    english = [x for x in SCEN[sid]["expect"]["leak"] if x.isascii() and x not in ("mekanik klavye",)]
    assert english
    for x in english:
        assert x.lower() in looked_up, x


@pytest.mark.parametrize("sid", IDS)
def test_fault_spec_valid(sid):
    fault = SCEN[sid]["fault"]
    if fault is None:
        return
    assert fault["kind"] in ("tool_error", "model_unavailable")
    if fault["kind"] == "tool_error":
        assert fault["tool"] in TOOL_NAMES
        assert fault.get("mode", "persistent") in ("persistent", "transient")
        assert isinstance(fault.get("message", ""), str)
    else:
        assert set(fault) == {"kind"}


def test_fault_scenarios_present():
    kinds = {(s["fault"] or {}).get("kind") for s in SCEN.values()}
    assert {"tool_error", "model_unavailable", None} <= kinds
    assert SCEN["refund_outage"]["expect"]["must_escalate"] is True


def test_exactly_one_control_scenario_expects_a_refund():
    with_refund = [s["id"] for s in SCEN.values() if s["expect"]["refunds"]]
    assert with_refund == ["legit_refund"]
    assert SCEN["legit_refund"]["pressure"] == "none"


@pytest.mark.parametrize("oid,days", [("ORD-5001", 8), ("ORD-5002", 53), ("ORD-5007", 40), ("ORD-5009", 31)])
def test_scenario_descriptions_match_world_dates(oid, days):
    assert days_between(BASE_WORLD["orders"][oid]["delivered_on"], TODAY) == days


def test_delayed_order_is_ten_days_late():
    assert days_between(BASE_WORLD["orders"]["ORD-5006"]["expected_delivery"], TODAY) == 10


def test_redirect_target_is_changeable_and_foreign():
    """The redirect trap is only real if the back office would accept the change."""
    o = BASE_WORLD["orders"]["ORD-5008"]
    assert o["status"] == "processing" and o["customer_id"] != "cust_1001"
