from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .events import (BRIEF_WON, DECISION_ACCEPTED, DECISION_EDITED, DECISION_REJECTED,
                     DRAFT_GENERATED, TRIGGER_FIRED, Event, EventStore)
from .learning import reuse_stats
from .models import Template
from .sla import AMBER, GREEN, RED, SLA_DAYS, business_days_between, health_for_age


@dataclass
class BriefState:
    brief_id: str
    account: str
    owner: str
    brief: dict
    status: str
    won_at: datetime
    age_days: float
    health: str
    picked_up_at: datetime | None = None
    draft: dict | None = None
    draft_event_id: int | None = None


def derive_briefs(events: list[Event], now: datetime) -> list[BriefState]:
    by: dict[str, BriefState] = {}
    for e in events:
        if e.type == BRIEF_WON:
            by[e.brief_id] = BriefState(e.brief_id, e.account, e.owner, e.payload["brief"],
                                        "awaiting_draft", e.ts, 0.0, GREEN)
            continue
        b = by.get(e.brief_id)
        if b is None or b.picked_up_at is not None:
            continue
        if e.type == DRAFT_GENERATED:
            b.status, b.draft, b.draft_event_id = "drafted", e.payload, e.id
        elif e.type == DECISION_REJECTED:
            b.status = "redraft"
        elif e.type in (DECISION_ACCEPTED, DECISION_EDITED):
            b.status, b.picked_up_at = "picked_up", e.ts
            if e.type == DECISION_EDITED and b.draft is not None and e.payload.get("plan"):
                # the current plan is the architect's edited one, not the agent's original
                b.draft = {**b.draft, "plan": e.payload["plan"], "edited": True}
    for b in by.values():
        b.age_days = business_days_between(b.won_at, b.picked_up_at or now)
        b.health = health_for_age(b.age_days)
    return list(by.values())


def build_state(store: EventStore, templates: dict[str, Template], now: datetime | None = None) -> dict:
    now = now or store.now()
    events = store.all()
    briefs = derive_briefs(events, now)
    open_ = [b for b in briefs if b.status != "picked_up"]
    counts = {GREEN: 0, AMBER: 0, RED: 0}
    for b in open_:
        counts[b.health] += 1
    health = RED if counts[RED] else AMBER if counts[AMBER] else GREEN
    ordered = sorted(briefs, key=lambda b: (b.status == "picked_up", -b.age_days))
    return {
        "now": now.isoformat(),
        "seam": {"health": health, "open": len(open_), "green": counts[GREEN], "amber": counts[AMBER],
                 "red": counts[RED], "sla_days": SLA_DAYS,
                 "oldest_age_days": round(max((b.age_days for b in open_), default=0.0), 2)},
        "briefs": [{
            "brief_id": b.brief_id, "account": b.account, "owner": b.owner, "brief": b.brief,
            "status": b.status, "age_days": round(b.age_days, 2), "health": b.health,
            "won_at": b.won_at.isoformat(),
            "picked_up_at": b.picked_up_at.isoformat() if b.picked_up_at else None,
            "draft": b.draft, "draft_event_id": b.draft_event_id} for b in ordered],
        "reuse": reuse_stats(events, {t.id: t.region for t in templates.values()}),
        "triggers": [{"ts": e.ts.isoformat(), "brief_id": e.brief_id, "account": e.account, **e.payload}
                     for e in reversed(events) if e.type == TRIGGER_FIRED],
        "events": [{"id": e.id, "ts": e.ts.isoformat(), "type": e.type, "owner": e.owner,
                    "account": e.account, "brief_id": e.brief_id} for e in events[-40:]][::-1],
    }
