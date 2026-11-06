from __future__ import annotations

from .agent.graph import AgentRunner
from .events import EventStore
from .ingest import ingest_won, make_hubspot_won_payload
from .sample_briefs import SAMPLE_BRIEFS
from .sla import shift_back_business_days
from .watcher import tick

# SYNTHETIC: (sample brief key, sales rep, age in business days at boot)
SEED_PLAN = [
    ("meridian", "Priya Nair", 0.5),
    ("northbridge", "Priya Nair", 1.5),
    ("atlas", "Chris Alvarez", 3.0),
    ("keystone", "Chris Alvarez", 7.0),
]


def _won(store: EventStore, runner: AgentRunner, key: str, rep: str, age: float, oid: str) -> str | None:
    occurred = shift_back_business_days(store.now(), age) if age else store.now()
    payload = make_hubspot_won_payload(object_id=oid, rep=rep, brief=SAMPLE_BRIEFS[key],
                                       occurred_at=occurred)
    brief_id = ingest_won(store, payload)
    if brief_id:
        runner.start(brief_id)
    return brief_id


def seed(store: EventStore, runner: AgentRunner, slack_url: str = "") -> list[str]:
    ids = [_won(store, runner, key, rep, age, f"seed-{key}") for key, rep, age in SEED_PLAN]
    tick(store, slack_url)
    return [i for i in ids if i]


def inject(store: EventStore, runner: AgentRunner) -> str:
    return _won(store, runner, "harbor", "Priya Nair", 0, f"live-{len(store.all())}")
