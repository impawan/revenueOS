from datetime import datetime, timedelta, timezone

from revenueos.events import (BRIEF_WON, CLOCK_ADVANCED, EventStore)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def make():
    return EventStore(real_now=lambda: NOW)


def test_append_and_read_back_in_order():
    s = make()
    a = s.append(BRIEF_WON, "Priya", "Acme", "b1", {"x": 1})
    b = s.append("draft.generated", "agent", "Acme", "b1", {"y": [1, 2]})
    events = s.all()
    assert [e.id for e in events] == [a.id, b.id]
    assert events[0].payload == {"x": 1}
    assert events[1].payload == {"y": [1, 2]}
    assert events[0].ts == NOW


def test_explicit_ts_is_kept():
    s = make()
    ts = NOW - timedelta(days=3)
    e = s.append(BRIEF_WON, "Priya", "Acme", "b1", {}, ts=ts)
    assert s.all()[0].ts == ts and e.ts == ts


def test_for_brief_filters():
    s = make()
    s.append(BRIEF_WON, "p", "A", "b1", {})
    s.append(BRIEF_WON, "p", "B", "b2", {})
    assert [e.brief_id for e in s.for_brief("b2")] == ["b2"]


def test_clock_advance_shifts_now():
    s = make()
    assert s.now() == NOW
    s.append(CLOCK_ADVANCED, "demo", "-", "-", {"hours": 24})
    s.append(CLOCK_ADVANCED, "demo", "-", "-", {"hours": 12})
    assert s.clock_offset() == timedelta(hours=36)
    assert s.now() == NOW + timedelta(hours=36)


def test_reset_clears_everything():
    s = make()
    s.append(CLOCK_ADVANCED, "demo", "-", "-", {"hours": 24})
    s.reset()
    assert s.all() == [] and s.now() == NOW
