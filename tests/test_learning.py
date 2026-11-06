from datetime import datetime, timezone

from revenueos.events import (DECISION_ACCEPTED, DECISION_EDITED, DECISION_REJECTED, EventStore)
from revenueos.learning import reuse_stats, template_boosts

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def mk():
    return EventStore(real_now=lambda: NOW)


def dec(s, typ, ids):
    s.append(typ, "Marcus Reyes", "Acme", "b1", {"template_ids": ids})


def test_accept_boosts_reject_penalises():
    s = mk()
    dec(s, DECISION_ACCEPTED, ["A"])
    dec(s, DECISION_EDITED, ["A", "B"])
    dec(s, DECISION_REJECTED, ["B"])
    b = template_boosts(s.all())
    assert b["A"] == 0.2
    assert b["B"] == round(0.1 - 0.15, 4)


def test_boost_is_clamped():
    s = mk()
    for _ in range(6):
        dec(s, DECISION_ACCEPTED, ["A"])
    for _ in range(6):
        dec(s, DECISION_REJECTED, ["B"])
    b = template_boosts(s.all())
    assert b["A"] == 0.30 and b["B"] == -0.30


def test_no_decisions_no_boosts():
    assert template_boosts(mk().all()) == {}


def test_reuse_stats():
    s = mk()
    dec(s, DECISION_ACCEPTED, ["TPL-UK-001"])
    dec(s, DECISION_ACCEPTED, [])
    dec(s, DECISION_EDITED, ["TPL-US-003"])
    dec(s, DECISION_REJECTED, ["TPL-UK-001"])
    st = reuse_stats(s.all(), {"TPL-UK-001": "UK", "TPL-US-003": "US"})
    assert st["decisions"] == 3 and st["reused"] == 2 and st["rejections"] == 1
    assert st["rate"] == round(2 / 3, 3)
    assert st["cross_region"] == 1 and st["cross_region_rate"] == round(1 / 3, 3)


def test_reuse_stats_empty():
    st = reuse_stats([], {})
    assert st["decisions"] == 0 and st["rate"] == 0.0 and st["cross_region_rate"] == 0.0
