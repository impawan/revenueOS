from __future__ import annotations

from datetime import datetime, timedelta

GREEN, AMBER, RED = "green", "amber", "red"
SLA_DAYS = 2.0
AMBER_DAYS = 1.0


def business_days_between(start: datetime, end: datetime) -> float:
    if end <= start:
        return 0.0
    total, cur = 0.0, start
    while cur < end:
        next_midnight = cur.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        seg_end = min(next_midnight, end)
        if cur.weekday() < 5:
            total += (seg_end - cur).total_seconds() / 86400
        cur = seg_end
    return total


def health_for_age(age_days: float) -> str:
    if age_days >= SLA_DAYS:
        return RED
    if age_days >= AMBER_DAYS:
        return AMBER
    return GREEN


def shift_back_business_days(end: datetime, days: float) -> datetime:
    remaining, cur = days, end
    while remaining > 1e-9:
        midnight = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = midnight if cur > midnight else midnight - timedelta(days=1)
        if day_start.weekday() < 5:
            length = (cur - day_start).total_seconds() / 86400
            if length >= remaining:
                return cur - timedelta(days=remaining)
            remaining -= length
        cur = day_start
    return cur
