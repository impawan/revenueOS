import pytest

from revenueos.events import BRIEF_WON
from revenueos.ingest import ingest_won, make_hubspot_won_payload, parse_hubspot_won
from revenueos.sample_briefs import SAMPLE_BRIEFS


def payload(now, oid="deal-1"):
    return make_hubspot_won_payload(object_id=oid, rep="Priya Nair",
                                    brief=SAMPLE_BRIEFS["northbridge"], occurred_at=now)


def test_payload_looks_like_hubspot(now):
    p = payload(now)
    assert p["subscriptionType"] == "deal.propertyChange"
    assert p["propertyName"] == "dealstage" and p["propertyValue"] == "closedwon"
    assert isinstance(p["occurredAt"], int) and p["objectId"] == "deal-1"
    assert p["deal"]["properties"]["brief_systems"] == "Actimize;Salesforce"


def test_parse_round_trip(now):
    w = parse_hubspot_won(payload(now))
    assert w.brief_id == "brief-deal-1" and w.owner == "Priya Nair"
    assert w.account == "Northbridge Bank"
    assert w.brief == SAMPLE_BRIEFS["northbridge"]
    assert w.occurred_at == now


def test_non_won_is_rejected(now):
    p = payload(now)
    p["propertyValue"] = "contractsent"
    with pytest.raises(ValueError):
        parse_hubspot_won(p)


def test_missing_brief_fields_rejected(now):
    p = payload(now)
    del p["deal"]["properties"]["brief_problem"]
    with pytest.raises(ValueError):
        parse_hubspot_won(p)


def test_ingest_appends_event_and_is_idempotent(store, now):
    p = payload(now)
    assert ingest_won(store, p) == "brief-deal-1"
    assert ingest_won(store, p) is None
    events = [e for e in store.all() if e.type == BRIEF_WON]
    assert len(events) == 1
    e = events[0]
    assert e.owner == "Priya Nair" and e.account == "Northbridge Bank" and e.ts == now
    assert e.payload["brief"]["regulator"] == "OCC"
    assert e.payload["deal"]["objectId"] == "deal-1"
