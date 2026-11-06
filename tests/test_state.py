from datetime import timedelta

from revenueos.events import (BRIEF_WON, CLOCK_ADVANCED, DECISION_ACCEPTED, DECISION_REJECTED,
                              DRAFT_GENERATED, TRIGGER_FIRED)
from revenueos.sla import shift_back_business_days
from revenueos.state import build_state, derive_briefs

BRIEF = {"account": "Acme", "region": "US", "segment": "insurer", "regulator": "NAIC",
         "problem": "p", "systems": [], "timeline": "", "success": ""}


def won(store, bid, age):
    ts = shift_back_business_days(store.now(), age)
    store.append(BRIEF_WON, "Priya", "Acme", bid, {"brief": BRIEF}, ts=ts)


def test_status_transitions(store):
    won(store, "b1", 0.5)
    assert derive_briefs(store.all(), store.now())[0].status == "awaiting_draft"
    store.append(DRAFT_GENERATED, "agent", "Acme", "b1", {"plan": {}, "mode": "fallback"})
    assert derive_briefs(store.all(), store.now())[0].status == "drafted"
    store.append(DECISION_REJECTED, "Marcus", "Acme", "b1", {"template_ids": []})
    assert derive_briefs(store.all(), store.now())[0].status == "redraft"
    store.append(DRAFT_GENERATED, "agent", "Acme", "b1", {"plan": {}, "mode": "fallback"})
    store.append(DECISION_ACCEPTED, "Marcus", "Acme", "b1", {"template_ids": []})
    b = derive_briefs(store.all(), store.now())[0]
    assert b.status == "picked_up" and b.picked_up_at is not None


def test_age_and_health_for_open_brief(store):
    won(store, "g", 0.5)
    won(store, "a", 1.5)
    won(store, "r", 3)
    by = {b.brief_id: b for b in derive_briefs(store.all(), store.now())}
    assert (by["g"].health, by["a"].health, by["r"].health) == ("green", "amber", "red")
    assert abs(by["r"].age_days - 3) < 0.01


def test_picked_up_age_is_frozen_at_pickup(store):
    won(store, "b1", 1.5)
    store.append(DRAFT_GENERATED, "agent", "Acme", "b1", {"plan": {}})
    store.append(DECISION_ACCEPTED, "Marcus", "Acme", "b1", {"template_ids": []})
    store.append(CLOCK_ADVANCED, "demo", "-", "-", {"hours": 24 * 10})
    b = derive_briefs(store.all(), store.now())[0]
    assert abs(b.age_days - 1.5) < 0.01 and b.health == "amber"


def test_build_state_seam_rollup_and_logs(store, templates):
    won(store, "g", 0.5)
    won(store, "r", 3)
    store.append(DRAFT_GENERATED, "agent", "Acme", "r", {"plan": {"objective": "x"}, "mode": "fallback"})
    store.append(TRIGGER_FIRED, "watcher", "Acme", "r", {"recipient": "Dana Whitfield"})
    st = build_state(store, {t.id: t for t in templates})
    assert st["seam"]["health"] == "red"
    assert (st["seam"]["green"], st["seam"]["amber"], st["seam"]["red"]) == (1, 0, 1)
    assert st["seam"]["open"] == 2 and st["seam"]["sla_days"] == 2.0
    assert st["briefs"][0]["brief_id"] == "r"  # oldest open brief first
    assert st["briefs"][0]["draft"]["plan"]["objective"] == "x"
    assert st["triggers"][0]["recipient"] == "Dana Whitfield"
    assert st["reuse"]["decisions"] == 0
    assert st["events"] and st["now"]


def test_empty_state_is_green(store, templates):
    st = build_state(store, {t.id: t for t in templates})
    assert st["seam"]["health"] == "green" and st["briefs"] == []
