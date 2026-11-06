from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

BRIEF_WON = "brief.won"
DRAFT_GENERATED = "draft.generated"
DECISION_ACCEPTED = "decision.accepted"
DECISION_EDITED = "decision.edited"
DECISION_REJECTED = "decision.rejected"
SLA_AMBER = "sla.amber"
SLA_BREACHED = "sla.breached"
TRIGGER_FIRED = "trigger.fired"
CLOCK_ADVANCED = "clock.advanced"


@dataclass(frozen=True)
class Event:
    id: int
    ts: datetime
    type: str
    owner: str
    account: str
    brief_id: str
    payload: dict


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventStore:
    """Append-only event log. The demo clock is itself derived from events."""

    def __init__(self, path: str = ":memory:", real_now: Callable[[], datetime] = _utcnow):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._real_now = real_now
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS os_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, type TEXT NOT NULL, owner TEXT NOT NULL,
                account TEXT NOT NULL, brief_id TEXT NOT NULL, payload_json TEXT NOT NULL)"""
        )
        self._db.commit()

    def append(self, type: str, owner: str, account: str, brief_id: str,
               payload: dict | None = None, ts: datetime | None = None) -> Event:
        with self._lock:
            ts = ts or self.now()
            cur = self._db.execute(
                "INSERT INTO os_events(ts,type,owner,account,brief_id,payload_json) VALUES(?,?,?,?,?,?)",
                (ts.isoformat(), type, owner, account, brief_id, json.dumps(payload or {})),
            )
            self._db.commit()
            return Event(cur.lastrowid, ts, type, owner, account, brief_id, payload or {})

    def _rows(self, where: str = "", args: tuple = ()) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id,ts,type,owner,account,brief_id,payload_json FROM os_events "
                + where + " ORDER BY id", args).fetchall()
        return [Event(r[0], datetime.fromisoformat(r[1]), r[2], r[3], r[4], r[5], json.loads(r[6]))
                for r in rows]

    def all(self) -> list[Event]:
        return self._rows()

    def for_brief(self, brief_id: str) -> list[Event]:
        return self._rows("WHERE brief_id=?", (brief_id,))

    def clock_offset(self) -> timedelta:
        hours = sum(e.payload.get("hours", 0) for e in self._rows("WHERE type=?", (CLOCK_ADVANCED,)))
        return timedelta(hours=hours)

    def now(self) -> datetime:
        return self._real_now() + self.clock_offset()

    def reset(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM os_events")
            self._db.commit()
