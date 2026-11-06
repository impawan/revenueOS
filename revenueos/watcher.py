from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Callable

from .config import ESCALATION_CHANNEL, ESCALATION_OWNER, ESCALATION_ROLE
from .events import SLA_AMBER, SLA_BREACHED, TRIGGER_FIRED, Event, EventStore
from .sla import AMBER_DAYS, SLA_DAYS
from .state import BriefState, derive_briefs

WATCHER = "revenueos-watcher"
log = logging.getLogger("revenueos.watcher")


def _post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=5).read()


def _fire_trigger(store: EventStore, b: BriefState, slack_url: str,
                  post: Callable[[str, dict], None]) -> Event:
    plan = (b.draft or {}).get("plan")
    objective = plan["objective"] if plan else None
    msg = (f"SLA breach: {b.account} brief waiting {b.age_days:.1f} business days "
           f"(SLA {SLA_DAYS:.0f}). "
           + (f"Draft POC plan attached ({b.draft['mode']})." if plan else "No draft yet."))
    slack_payload = {
        "text": f"<!channel> {msg} Owner: {ESCALATION_OWNER}.",
        "attachments": [{"title": f"POC plan draft for {b.account}",
                         "text": objective or "Agent has not produced a draft."}],
    }
    delivery = "logged"
    if slack_url:
        try:
            post(slack_url, slack_payload)
            delivery = "posted"
        except Exception as e:
            delivery = f"failed: {e}"
    return store.append(TRIGGER_FIRED, WATCHER, b.account, b.brief_id, {
        "recipient": ESCALATION_OWNER, "role": ESCALATION_ROLE, "channel": ESCALATION_CHANNEL,
        "message": msg, "draft_event_id": b.draft_event_id, "slack_payload": slack_payload,
        "delivery": delivery})


def tick(store: EventStore, slack_url: str = "", post: Callable[[str, dict], None] | None = None) -> list[Event]:
    post = post or _post_json
    now = store.now()
    events = store.all()
    seen = {(e.brief_id, e.type) for e in events if e.type in (SLA_AMBER, SLA_BREACHED)}
    emitted: list[Event] = []
    for b in derive_briefs(events, now):
        if b.status == "picked_up":
            continue
        age = round(b.age_days, 2)
        if b.age_days >= AMBER_DAYS and (b.brief_id, SLA_AMBER) not in seen:
            emitted.append(store.append(SLA_AMBER, WATCHER, b.account, b.brief_id,
                                        {"age_days": age, "threshold": AMBER_DAYS}))
        if b.age_days >= SLA_DAYS and (b.brief_id, SLA_BREACHED) not in seen:
            emitted.append(store.append(SLA_BREACHED, WATCHER, b.account, b.brief_id,
                                        {"age_days": age, "threshold": SLA_DAYS}))
            emitted.append(_fire_trigger(store, b, slack_url, post))
    return emitted


async def watch_forever(store: EventStore, interval: float, slack_url: str = "") -> None:
    while True:
        try:
            await asyncio.to_thread(tick, store, slack_url)
        except Exception:
            log.exception("watcher tick failed")
        await asyncio.sleep(interval)
