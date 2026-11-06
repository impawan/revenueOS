from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .events import BRIEF_WON, EventStore
from .models import Brief


class MalformedPayload(ValueError):
    """Payload has the wrong shape or types (as opposed to a valid but irrelevant event)."""


@dataclass
class WonDeal:
    brief_id: str
    owner: str
    account: str
    brief: Brief
    occurred_at: datetime


def make_hubspot_won_payload(*, object_id: str, rep: str, brief: Brief, occurred_at: datetime) -> dict:
    """Synthetic payload shaped like a HubSpot deal.propertyChange webhook with brief custom properties."""
    return {
        "subscriptionType": "deal.propertyChange",
        "portalId": 4821991,
        "objectId": object_id,
        "propertyName": "dealstage",
        "propertyValue": "closedwon",
        "occurredAt": int(occurred_at.timestamp() * 1000),
        "deal": {
            "owner": {"name": rep},
            "properties": {
                "dealname": f"{brief.account} - Platform",
                "region": brief.region,
                "brief_segment": brief.segment,
                "brief_regulator": brief.regulator,
                "brief_problem": brief.problem,
                "brief_systems": ";".join(brief.systems),
                "brief_timeline": brief.timeline,
                "brief_success": brief.success,
            },
            "associations": {"company": {"name": brief.account}},
        },
    }


def parse_hubspot_won(payload: dict) -> WonDeal:
    try:
        if payload["propertyName"] != "dealstage" or payload["propertyValue"] != "closedwon":
            raise ValueError("not a closedwon deal-stage change")
        props = payload["deal"]["properties"]
        brief = Brief(
            account=payload["deal"]["associations"]["company"]["name"],
            region=props["region"], segment=props["brief_segment"],
            regulator=props["brief_regulator"], problem=props["brief_problem"],
            systems=[s for s in props.get("brief_systems", "").split(";") if s],
            timeline=props.get("brief_timeline", ""), success=props.get("brief_success", ""))
        return WonDeal(
            brief_id=f"brief-{payload['objectId']}",
            owner=payload["deal"]["owner"]["name"],
            account=brief.account, brief=brief,
            occurred_at=datetime.fromtimestamp(payload["occurredAt"] / 1000, tz=timezone.utc))
    except KeyError as e:
        raise ValueError(f"missing field in CRM payload: {e}") from e
    except (TypeError, AttributeError, OverflowError, OSError) as e:
        raise MalformedPayload(f"malformed CRM payload: {type(e).__name__}: {e}") from e


def ingest_won(store: EventStore, payload: dict) -> str | None:
    deal = parse_hubspot_won(payload)
    if any(e.type == BRIEF_WON for e in store.for_brief(deal.brief_id)):
        return None  # HubSpot retries webhooks; ingest is idempotent
    store.append(BRIEF_WON, deal.owner, deal.account, deal.brief_id,
                 {"brief": deal.brief.model_dump(), "deal": payload}, ts=deal.occurred_at)
    return deal.brief_id
