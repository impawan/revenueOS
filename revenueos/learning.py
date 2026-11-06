from __future__ import annotations

from .events import DECISION_ACCEPTED, DECISION_EDITED, DECISION_REJECTED, Event

ACCEPT_BOOST = 0.10
REJECT_PENALTY = 0.15
CAP = 0.30


def template_boosts(events: list[Event]) -> dict[str, float]:
    raw: dict[str, float] = {}
    for e in events:
        if e.type in (DECISION_ACCEPTED, DECISION_EDITED):
            delta = ACCEPT_BOOST
        elif e.type == DECISION_REJECTED:
            delta = -REJECT_PENALTY
        else:
            continue
        for tid in e.payload.get("template_ids", []):
            raw[tid] = raw.get(tid, 0.0) + delta
    return {t: max(-CAP, min(CAP, round(v, 4))) for t, v in raw.items()}


def reuse_stats(events: list[Event], template_regions: dict[str, str], home_region: str = "US") -> dict:
    decided = [e for e in events if e.type in (DECISION_ACCEPTED, DECISION_EDITED)]
    rejections = sum(1 for e in events if e.type == DECISION_REJECTED)
    reused = [e for e in decided if e.payload.get("template_ids")]
    cross = [e for e in reused
             if any(template_regions.get(t, home_region) != home_region for t in e.payload["template_ids"])]
    n = len(decided)
    return {
        "decisions": n,
        "reused": len(reused),
        "rate": round(len(reused) / n, 3) if n else 0.0,
        "cross_region": len(cross),
        "cross_region_rate": round(len(cross) / n, 3) if n else 0.0,
        "rejections": rejections,
    }
