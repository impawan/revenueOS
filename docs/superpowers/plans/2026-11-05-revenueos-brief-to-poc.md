# RevenueOS Brief-to-POC-Plan Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working loop where a won-deal event triggers an AI agent to draft a POC plan and Delivery handoff from a template library, a US Solution Architect accepts/edits/rejects it on one screen, decisions train template ranking, and the seam computes its own SLA health and fires escalation triggers.

**Architecture:** Append-only SQLite `os_events` is the only source of truth; queue, health, reuse rate and ranking boosts are derived from it. A LangGraph flow (`retrieve → draft_llm → validate/retry → fallback → record → interrupt for human → decision event`) does the agent work; SLA watcher, triggers, ingest and learning are plain Python. FastAPI serves the webhook, JSON state, actions and one static HTML control surface.

**Tech Stack:** Python (3.12+), FastAPI + Uvicorn, SQLite (stdlib), LangGraph (`MemorySaver`), Anthropic SDK (direct call inside a node), Pydantic v2, hand-rolled BM25, vanilla JS, pytest + httpx.

**Spec:** `docs/superpowers/specs/2026-11-05-revenueos-brief-to-poc-design.md`

## Global Constraints

- Time-box: 8 hours total. Budget: T0 0.5h · T1–T5 2h · T6–T9 2h · T10–T11 1.25h · T12 1.25h · T13 1h. Cut scope, not hours.
- Everything synthetic is declared synthetic (briefs, templates, people, accounts, HubSpot payloads).
- `os_events` is the only table of facts. Nothing derived is stored; nothing is typed by hand.
- SLA: 2 business days (Mon–Fri). Green < 1.0, amber 1.0 to < 2.0, red ≥ 2.0. Clock stops at the first accepted/edited decision; a rejection keeps it running.
- Named people (synthetic): Solution Architect **Marcus Reyes**; breach escalation **Dana Whitfield**, US PreSales Director, channel `#us-presales-escalations`.
- LLM output is always validated against Pydantic; invalid twice → deterministic fallback. Mode (`llm`/`fallback`) is stored and shown.
- Model ID comes from `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`). Never read or commit `.env`; commit `.env.example` only.
- Every commit message ends with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Reference date in tests: Thursday 2026-11-05 12:00 UTC (Mon 2026-11-02, Fri 2026-11-06, Mon 2026-11-09).

## File Structure

```
revenueos/
  __init__.py
  config.py          settings, named owners, .env loader
  models.py          Pydantic: Brief, Template, Candidate, PocPlan, HandoffDoc, DraftOutput
  events.py          Event, EventStore (append-only + demo clock), event type constants
  sla.py             business-day math, health thresholds
  library.py         load_templates()
  data/templates.json   12 synthetic templates
  sample_briefs.py   SAMPLE_BRIEFS (5 synthetic briefs)
  retrieval.py       Retriever (BM25 + filters + boost) with match reasons
  learning.py        template_boosts(), reuse_stats()
  state.py           derive_briefs(), build_state()
  ingest.py          HubSpot-shaped payload build/parse, ingest_won()
  agent/__init__.py
  agent/fallback.py  draft_fallback()
  agent/llm.py       draft_llm()
  agent/graph.py     AgentDeps, AgentRunner (LangGraph)
  watcher.py         tick(), watch_forever()
  seed.py            seed(), inject()
  api.py             create_app()
  static/index.html  control surface
tests/               one test file per module + conftest.py, fakes.py
docs/                spec, plan, diagrams, build-log, cut-list
```

---

### Task 0: Scaffold, environment, LangGraph check

**Files:**
- Create: `.gitignore`, `requirements.txt`, `pytest.ini`, `.env.example`, `revenueos/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: working venv at `.venv`, importable `revenueos` package, `pytest` runs.

- [ ] **Step 1: Init git and venv**

```bash
cd /Users/prasadpawan/project/revenueos
git init
python3 -m venv .venv
source .venv/bin/activate
```

- [ ] **Step 2: Write config files**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.env
data/
.pytest_cache/
```

`requirements.txt`:
```
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.8
anthropic>=0.40
langgraph>=0.2.60
httpx>=0.27
pytest>=8
```

`pytest.ini`:
```
[pytest]
pythonpath = .
testpaths = tests
```

`.env.example`:
```
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5-5
AGENT_MODE=auto
SLACK_WEBHOOK_URL=
WATCHER_INTERVAL=5
```

`revenueos/__init__.py`: empty file.

`tests/test_smoke.py`:
```python
def test_langgraph_interrupt_roundtrip():
    from typing import TypedDict
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    class S(TypedDict, total=False):
        answer: str

    def ask(state):
        return {"answer": interrupt("need input")}

    g = StateGraph(S)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    graph.invoke({}, cfg)
    assert graph.get_state(cfg).next == ("ask",)
    out = graph.invoke(Command(resume="yes"), cfg)
    assert out["answer"] == "yes"
```

- [ ] **Step 3: Install and run**

Run: `pip install -r requirements.txt && pytest -v`
Expected: PASS. If `pip install` fails on the local Python (3.14), run `brew install python@3.13`, recreate the venv with `python3.13 -m venv .venv`, and repeat. Do not proceed until `test_langgraph_interrupt_roundtrip` passes. If the `interrupt`/`Command` import paths differ in the installed version, adjust this test and note the right paths for Task 9.

- [ ] **Step 4: Commit**

```bash
git add .gitignore requirements.txt pytest.ini .env.example revenueos tests docs
git commit -m "chore: scaffold project, verify langgraph interrupt"
```

---

### Task 1: Models and event store

**Files:**
- Create: `revenueos/models.py`, `revenueos/events.py`, `tests/test_events.py`

**Interfaces:**
- Produces (`models.py`): `Brief`, `Template`, `Candidate`, `TemplateUse`, `WeekPlan`, `PocPlan`, `HandoffDoc`, `DraftOutput` (field lists below).
- Produces (`events.py`): constants `BRIEF_WON, DRAFT_GENERATED, DECISION_ACCEPTED, DECISION_EDITED, DECISION_REJECTED, SLA_AMBER, SLA_BREACHED, TRIGGER_FIRED, CLOCK_ADVANCED`; `Event` dataclass `(id:int, ts:datetime, type:str, owner:str, account:str, brief_id:str, payload:dict)`; `EventStore(path=":memory:", real_now=...)` with `append(type, owner, account, brief_id, payload=None, ts=None) -> Event`, `all() -> list[Event]`, `for_brief(brief_id) -> list[Event]`, `clock_offset() -> timedelta`, `now() -> datetime`, `reset() -> None`.

- [ ] **Step 1: Write the failing test** `tests/test_events.py`

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_events.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.events`)

- [ ] **Step 3: Implement** `revenueos/models.py`

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class Brief(BaseModel):
    account: str
    region: str
    segment: str  # retail bank | insurer | capital markets | lender
    regulator: str
    problem: str
    systems: list[str] = Field(default_factory=list)
    timeline: str = ""
    success: str = ""


class Template(BaseModel):
    id: str
    name: str
    region: str
    segment: str
    regulator: str
    problem: str
    capabilities: list[str]
    integrations: list[str]
    effort_weeks: int
    outcome: str
    last_used: str
    owner: str


class Candidate(BaseModel):
    template_id: str
    name: str
    region: str
    score: float
    reasons: list[str]


class TemplateUse(BaseModel):
    template_id: str
    what_changes: str


class WeekPlan(BaseModel):
    week: int
    focus: str
    activities: list[str]


class PocPlan(BaseModel):
    objective: str
    success_criteria: list[str]
    scope_in: list[str]
    scope_out: list[str]
    templates_drawn_on: list[TemplateUse]
    integrations: list[str]
    week_by_week: list[WeekPlan]
    risks: list[str]
    people_needed: list[str]


class HandoffDoc(BaseModel):
    customer: str
    regulatory_context: str
    systems: list[str]
    commitments: list[str]
    open_questions: list[str]
    missing_from_sales: list[str]
    delivery_prerequisites: list[str]


class DraftOutput(BaseModel):
    plan: PocPlan
    handoff: HandoffDoc
```

`revenueos/events.py`

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_events.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add revenueos/models.py revenueos/events.py tests/test_events.py
git commit -m "feat: pydantic models and append-only event store with demo clock"
```

---

### Task 2: SLA math

**Files:**
- Create: `revenueos/sla.py`, `tests/test_sla.py`

**Interfaces:**
- Produces: `GREEN="green"`, `AMBER="amber"`, `RED="red"`, `SLA_DAYS=2.0`, `AMBER_DAYS=1.0`; `business_days_between(start: datetime, end: datetime) -> float`; `health_for_age(age_days: float) -> str`; `shift_back_business_days(end: datetime, days: float) -> datetime` (used by seeding to backdate briefs).

- [ ] **Step 1: Write the failing test** `tests/test_sla.py`

```python
from datetime import datetime, timezone

import pytest

from revenueos.sla import (AMBER, GREEN, RED, business_days_between, health_for_age,
                           shift_back_business_days)


def d(day, hour=12):
    return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)


def test_one_weekday():
    assert business_days_between(d(2), d(3)) == pytest.approx(1.0)  # Mon 12:00 -> Tue 12:00


def test_weekend_is_skipped():
    assert business_days_between(d(6), d(9)) == pytest.approx(1.0)  # Fri 12:00 -> Mon 12:00


def test_pure_weekend_is_zero():
    assert business_days_between(d(7, 0), d(8, 23)) == 0.0


def test_end_before_start_is_zero():
    assert business_days_between(d(3), d(2)) == 0.0


@pytest.mark.parametrize("age,expected", [
    (0.0, GREEN), (0.99, GREEN), (1.0, AMBER), (1.99, AMBER), (2.0, RED), (6.8, RED)])
def test_health_thresholds(age, expected):
    assert health_for_age(age) == expected


def test_shift_back_simple_and_over_weekend():
    assert shift_back_business_days(d(3), 1.0) == d(2)
    assert shift_back_business_days(d(9), 1.0) == d(6)


@pytest.mark.parametrize("days", [0.5, 1.5, 3, 7])
def test_round_trip(days):
    now = d(5)
    assert business_days_between(shift_back_business_days(now, days), now) == pytest.approx(days)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sla.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement** `revenueos/sla.py`

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_sla.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add revenueos/sla.py tests/test_sla.py
git commit -m "feat: business-day SLA math and green/amber/red thresholds"
```

---

### Task 3: Template library, sample briefs, retrieval

**Files:**
- Create: `revenueos/library.py`, `revenueos/data/templates.json`, `revenueos/sample_briefs.py`, `revenueos/retrieval.py`, `tests/conftest.py`, `tests/test_retrieval.py`

**Interfaces:**
- Consumes: `Brief`, `Template`, `Candidate` from `models.py`.
- Produces: `load_templates(path=None) -> list[Template]`; `SAMPLE_BRIEFS: dict[str, Brief]` with keys `meridian` (claims), `northbridge` (KYC), `atlas` (underwriting), `keystone` (regulatory reporting), `harbor` (fraud); `Retriever(templates)` with `.templates: dict[str, Template]` and `.search(brief, boosts=None, k=3) -> list[Candidate]` (score = 0.6·BM25_norm + 0.3·filter + boost; reasons are human-readable strings).

- [ ] **Step 1: Write the failing test** `tests/conftest.py` and `tests/test_retrieval.py`

`tests/conftest.py`:
```python
from datetime import datetime, timezone

import pytest

from revenueos.events import EventStore
from revenueos.library import load_templates
from revenueos.retrieval import Retriever

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)  # Thursday


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def store():
    return EventStore(real_now=lambda: NOW)


@pytest.fixture
def templates():
    return load_templates()


@pytest.fixture
def retriever(templates):
    return Retriever(templates)
```

`tests/test_retrieval.py`:
```python
from revenueos.library import load_templates
from revenueos.retrieval import Retriever
from revenueos.sample_briefs import SAMPLE_BRIEFS


def test_library_shape():
    ts = load_templates()
    assert len(ts) >= 8
    assert len({t.region for t in ts}) >= 3
    assert len({t.id for t in ts}) == len(ts)


def ids(cands):
    return [c.template_id for c in cands]


def test_fraud_brief_finds_india_fraud_template(retriever):
    assert ids(retriever.search(SAMPLE_BRIEFS["harbor"]))[0] == "TPL-IN-009"


def test_kyc_brief_surfaces_cross_region_kyc_template(retriever):
    top = retriever.search(SAMPLE_BRIEFS["northbridge"])
    assert ids(top)[0] in {"TPL-UK-001", "TPL-IN-002"}
    assert top[0].region != "US"


def test_claims_brief_top2_has_claims_triage(retriever):
    assert {"TPL-IN-005", "TPL-UK-006"} & set(ids(retriever.search(SAMPLE_BRIEFS["meridian"]))[:2])


def test_underwriting_and_regreporting(retriever):
    assert ids(retriever.search(SAMPLE_BRIEFS["atlas"]))[0] in {"TPL-US-003", "TPL-UK-004"}
    assert ids(retriever.search(SAMPLE_BRIEFS["keystone"]))[0] == "TPL-UK-007"


def test_every_candidate_explains_itself(retriever):
    for c in retriever.search(SAMPLE_BRIEFS["northbridge"]):
        assert c.reasons and all(isinstance(r, str) and r for r in c.reasons)
    top = retriever.search(SAMPLE_BRIEFS["northbridge"])[0]
    assert any(r.startswith("shared terms:") for r in top.reasons)


def test_negative_boost_lowers_rank_and_is_explained(retriever):
    brief = SAMPLE_BRIEFS["harbor"]
    base = retriever.search(brief, k=12)
    top_id = base[0].template_id
    boosted = retriever.search(brief, boosts={top_id: -0.30}, k=12)
    assert boosted[0].template_id != top_id
    penal = next(c for c in boosted if c.template_id == top_id)
    assert penal.score < base[0].score
    assert any("learned boost" in r for r in penal.reasons)


def test_k_limits_results(retriever):
    assert len(retriever.search(SAMPLE_BRIEFS["harbor"], k=2)) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_retrieval.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.library`)

- [ ] **Step 3: Implement**

`revenueos/library.py`
```python
from __future__ import annotations

import json
from pathlib import Path

from .models import Template

DEFAULT_PATH = Path(__file__).parent / "data" / "templates.json"


def load_templates(path: str | Path | None = None) -> list[Template]:
    raw = json.loads(Path(path or DEFAULT_PATH).read_text())
    return [Template(**t) for t in raw]
```

`revenueos/data/templates.json` (SYNTHETIC)
```json
[
  {"id": "TPL-UK-001", "name": "KYC document review assistant", "region": "UK", "segment": "retail bank", "regulator": "FCA",
   "problem": "Analysts manually review identity documents and proof-of-address documents during customer onboarding; reviews are slow and inconsistent between analysts.",
   "capabilities": ["document extraction", "OCR", "entity matching", "sanctions screening", "case routing"],
   "integrations": ["Onfido", "Salesforce", "Actimize"], "effort_weeks": 6,
   "outcome": "Cut onboarding document review time by 48% with 92% extraction accuracy.", "last_used": "2026-08-14", "owner": "Oliver Grant"},
  {"id": "TPL-IN-002", "name": "KYC and video-KYC document verification", "region": "India", "segment": "retail bank", "regulator": "RBI",
   "problem": "Branch and digital onboarding needs identity document verification, liveness checks and audit trails at high volume.",
   "capabilities": ["document extraction", "OCR", "liveness check", "audit trail", "case routing"],
   "integrations": ["DigiLocker", "Finacle", "Salesforce"], "effort_weeks": 7,
   "outcome": "Onboarding turnaround reduced from 3 days to 4 hours.", "last_used": "2026-09-02", "owner": "Ananya Rao"},
  {"id": "TPL-US-003", "name": "Loan underwriting assistant", "region": "US", "segment": "lender", "regulator": "CFPB",
   "problem": "Underwriters assemble credit memos by hand from pay stubs, bank statements and bureau reports, then check each loan against policy.",
   "capabilities": ["income document extraction", "credit memo drafting", "policy rule check", "explainability"],
   "integrations": ["nCino", "Encompass", "Equifax"], "effort_weeks": 8,
   "outcome": "Underwriter memo preparation time down 40%.", "last_used": "2026-05-20", "owner": "Rachel Kim"},
  {"id": "TPL-UK-004", "name": "Mortgage affordability underwriting assist", "region": "UK", "segment": "lender", "regulator": "FCA",
   "problem": "Mortgage underwriters analyse bank statements and affordability by hand before issuing credit decisions.",
   "capabilities": ["bank statement analysis", "affordability calculation", "policy rule check", "credit memo drafting"],
   "integrations": ["Open Banking", "Mambu", "Experian"], "effort_weeks": 7,
   "outcome": "Affordability assessment time down 33%.", "last_used": "2026-07-11", "owner": "Tom Alder"},
  {"id": "TPL-IN-005", "name": "Claims triage and fraud flagging", "region": "India", "segment": "insurer", "regulator": "IRDAI",
   "problem": "Adjusters read claim intake emails, photos and documents to classify claims and decide severity; simple claims queue behind complex ones.",
   "capabilities": ["claim intake classification", "document extraction", "severity scoring", "fraud signal flagging", "routing"],
   "integrations": ["Guidewire ClaimCenter", "WhatsApp Business", "Duck Creek"], "effort_weeks": 6,
   "outcome": "Straight-through processing for 35% of motor claims.", "last_used": "2026-08-30", "owner": "Vikram Shah"},
  {"id": "TPL-UK-006", "name": "Property claims triage copilot", "region": "UK", "segment": "insurer", "regulator": "FCA",
   "problem": "Property claims handlers classify claims, assess photo evidence of damage and brief adjusters manually.",
   "capabilities": ["claim classification", "photo damage assessment", "adjuster summary", "routing"],
   "integrations": ["Guidewire ClaimCenter", "Salesforce"], "effort_weeks": 8,
   "outcome": "Claim cycle time down 22% on property claims.", "last_used": "2026-06-18", "owner": "Hannah Doyle"},
  {"id": "TPL-UK-007", "name": "Regulatory reporting automation", "region": "UK", "segment": "capital markets", "regulator": "FCA",
   "problem": "Transaction and position regulatory reports are stitched together from trade, reference and risk data in spreadsheets; reconciliation breaks surface at the deadline.",
   "capabilities": ["data lineage", "report generation", "reconciliation", "exception review"],
   "integrations": ["Snowflake", "Murex", "Axiom"], "effort_weeks": 10,
   "outcome": "Reports generated automatically with reconciliation exceptions flagged 3 days before deadline.", "last_used": "2026-04-09", "owner": "Edward Hale"},
  {"id": "TPL-US-008", "name": "SAR narrative drafting assistant", "region": "US", "segment": "retail bank", "regulator": "OCC",
   "problem": "Financial crime investigators write suspicious activity report narratives by hand from alert evidence.",
   "capabilities": ["alert summarisation", "SAR narrative drafting", "evidence retrieval", "audit trail"],
   "integrations": ["Actimize", "Fircosoft", "Salesforce"], "effort_weeks": 6,
   "outcome": "Narrative drafting time down 55%.", "last_used": "2026-09-15", "owner": "Jordan Blake"},
  {"id": "TPL-IN-009", "name": "Fraud alert triage", "region": "India", "segment": "retail bank", "regulator": "RBI",
   "problem": "Fraud analysts work thousands of daily card and wire alerts, most of them false positives, while high-risk alerts wait in the queue.",
   "capabilities": ["alert prioritisation", "transaction pattern explanation", "analyst queue routing", "feedback loop"],
   "integrations": ["FICO Falcon", "Finacle", "Kafka"], "effort_weeks": 5,
   "outcome": "False-positive review load down 38%.", "last_used": "2026-10-01", "owner": "Neha Iyer"},
  {"id": "TPL-ME-010", "name": "Bilingual customer service copilot", "region": "Middle East", "segment": "retail bank", "regulator": "CBUAE",
   "problem": "Contact-centre agents answer Arabic and English customer queries from scattered knowledge bases.",
   "capabilities": ["bilingual agent assist", "knowledge retrieval", "conversation summarisation", "compliance phrase check"],
   "integrations": ["Genesys", "Salesforce Service Cloud", "Temenos"], "effort_weeks": 8,
   "outcome": "Average handle time down 18%.", "last_used": "2026-07-25", "owner": "Layla Haddad"},
  {"id": "TPL-US-011", "name": "Insurance contact-centre agent assist", "region": "US", "segment": "insurer", "regulator": "NAIC",
   "problem": "Policy service agents search several systems during calls and write after-call notes by hand.",
   "capabilities": ["knowledge retrieval", "call summarisation", "next-best-action", "compliance check"],
   "integrations": ["Genesys", "Guidewire PolicyCenter", "Salesforce"], "effort_weeks": 6,
   "outcome": "After-call work down 45%.", "last_used": "2026-03-30", "owner": "Sam Ortega"},
  {"id": "TPL-ME-012", "name": "Trade surveillance alert review", "region": "Middle East", "segment": "capital markets", "regulator": "SCA",
   "problem": "Compliance officers review trade and communications surveillance alerts and document case decisions manually.",
   "capabilities": ["alert summarisation", "communication surveillance review", "evidence retrieval", "case documentation"],
   "integrations": ["Nasdaq SMARTS", "Bloomberg", "ServiceNow"], "effort_weeks": 9,
   "outcome": "Alert review time down 30%.", "last_used": "2026-05-05", "owner": "Omar Farouk"}
]
```

`revenueos/sample_briefs.py` (SYNTHETIC)
```python
from .models import Brief

SAMPLE_BRIEFS: dict[str, Brief] = {
    "meridian": Brief(
        account="Meridian Mutual", region="US", segment="insurer", regulator="NAIC",
        problem=("Adjusters spend hours reading first-notice-of-loss emails, photos and police reports "
                 "to classify auto and property claims and decide severity. Simple claims queue behind "
                 "complex ones and cycle time keeps growing."),
        systems=["Guidewire ClaimCenter", "Salesforce"],
        timeline="POC live in 10 weeks, pilot with 2 claims teams",
        success="Auto-classify 60% of new claims and route severity correctly with adjuster sign-off"),
    "northbridge": Brief(
        account="Northbridge Bank", region="US", segment="retail bank", regulator="OCC",
        problem=("Compliance analysts manually review passports, utility bills and proof-of-address "
                 "documents during account onboarding. Reviews take days and quality varies between analysts."),
        systems=["Actimize", "Salesforce"],
        timeline="Decision on pilot by Q1, 8-week POC",
        success="Cut document review time by 40% with analyst-level extraction accuracy"),
    "atlas": Brief(
        account="Atlas Credit", region="US", segment="lender", regulator="CFPB",
        problem=("Underwriters assemble credit memos by hand from pay stubs, bank statements and bureau "
                 "reports, then check each loan against policy. Memo preparation delays decisions on "
                 "small-business loans."),
        systems=["nCino", "Equifax", "Encompass"],
        timeline="POC in 8 weeks before the summer lending peak",
        success="Reduce memo preparation time by 35% and explain every policy check to the underwriter"),
    "keystone": Brief(
        account="Keystone Securities", region="US", segment="capital markets", regulator="SEC",
        problem=("Regulatory reports (transaction and position reporting) are stitched together from trade, "
                 "reference and risk data in spreadsheets. Reconciliation breaks surface late and reports "
                 "are filed at the deadline."),
        systems=["Snowflake", "Murex"],
        timeline="12-week POC aligned to the next filing cycle",
        success="Generate the report automatically with reconciliation exceptions flagged before the deadline"),
    "harbor": Brief(
        account="Harbor Trust Bank", region="US", segment="retail bank", regulator="OCC",
        problem=("Fraud analysts work thousands of daily card and wire alerts, most of them false positives. "
                 "High-risk alerts wait in the queue while analysts re-investigate the same patterns."),
        systems=["FICO Falcon", "Kafka"],
        timeline="6-week POC",
        success="Prioritise alerts so analysts review high-risk cases first and cut false-positive review load by 30%"),
}
```

`revenueos/retrieval.py`
```python
from __future__ import annotations

import math
import re
from collections import Counter

from .models import Brief, Candidate, Template

STOP = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "each", "then", "into",
        "while", "most", "them", "their", "by", "of", "to", "in", "on", "a", "an", "or", "is", "it",
        "at", "as", "be", "we", "our", "all", "per", "than", "before", "after", "work"}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]


class Retriever:
    def __init__(self, templates: list[Template]):
        self.templates = {t.id: t for t in templates}
        self._docs = {t.id: tokenize(self._text(t)) for t in templates}
        self._df: Counter = Counter()
        for toks in self._docs.values():
            for term in set(toks):
                self._df[term] += 1
        self._avgdl = sum(len(d) for d in self._docs.values()) / max(len(self._docs), 1)

    @staticmethod
    def _text(t: Template) -> str:
        return " ".join([t.name, t.problem, " ".join(t.capabilities), " ".join(t.integrations),
                         t.outcome, t.segment, t.regulator])

    def _idf(self, term: str) -> float:
        n, df = len(self._docs), self._df[term]
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _bm25(self, query: set[str], doc_id: str) -> float:
        k1, b = 1.5, 0.75
        toks = self._docs[doc_id]
        tf, dl, score = Counter(toks), len(toks), 0.0
        for q in query:
            if q in tf:
                score += self._idf(q) * tf[q] * (k1 + 1) / (tf[q] + k1 * (1 - b + b * dl / self._avgdl))
        return score

    @staticmethod
    def _shared_integrations(brief: Brief, t: Template) -> list[str]:
        out = []
        for s in brief.systems:
            for i in t.integrations:
                if s.lower() in i.lower() or i.lower() in s.lower():
                    out.append(i)
                    break
        return out

    def search(self, brief: Brief, boosts: dict[str, float] | None = None, k: int = 3) -> list[Candidate]:
        boosts = boosts or {}
        query = set(tokenize(" ".join([brief.problem, " ".join(brief.systems), brief.success,
                                       brief.segment, brief.regulator])))
        raw = {tid: self._bm25(query, tid) for tid in self._docs}
        top_raw = max(raw.values()) or 0.0
        out: list[Candidate] = []
        for tid, t in self.templates.items():
            bm = raw[tid] / top_raw if top_raw > 0 else 0.0
            seg = t.segment == brief.segment
            reg = t.regulator == brief.regulator
            shared_int = self._shared_integrations(brief, t)
            int_frac = len(shared_int) / len(brief.systems) if brief.systems else 0.0
            filt = 0.5 * seg + 0.3 * reg + 0.2 * int_frac
            boost = boosts.get(tid, 0.0)
            score = 0.6 * bm + 0.3 * filt + boost

            reasons: list[str] = []
            if seg:
                reasons.append(f"segment match: {t.segment}")
            if reg:
                reasons.append(f"regulator match: {t.regulator}")
            shared = sorted((q for q in query if q in set(self._docs[tid])), key=self._idf, reverse=True)[:5]
            if shared:
                reasons.append("shared terms: " + ", ".join(shared))
            if shared_int:
                reasons.append("integrations in common: " + ", ".join(shared_int))
            if t.region != brief.region:
                reasons.append(f"built in {t.region}; cross-region reuse")
            if boost:
                reasons.append(f"learned boost {boost:+.2f} from past decisions")
            if not reasons:
                reasons.append("weak lexical match")
            out.append(Candidate(template_id=tid, name=t.name, region=t.region,
                                 score=round(score, 3), reasons=reasons))
        out.sort(key=lambda c: c.score, reverse=True)
        return out[:k]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_retrieval.py -v`
Expected: all passed. If one ranking assertion fails, inspect `retriever.search(brief, k=5)` scores and adjust the **template text or brief wording** first (not the weights); weights are fixed by the spec.

- [ ] **Step 5: Commit**

```bash
git add revenueos tests
git commit -m "feat: synthetic template library, sample briefs, BM25 retrieval with reasons"
```

---

### Task 4: Learning from decisions

**Files:**
- Create: `revenueos/learning.py`, `tests/test_learning.py`

**Interfaces:**
- Consumes: `Event` and decision constants from `events.py`.
- Produces: `template_boosts(events: list[Event]) -> dict[str, float]` (+0.10 per accepted/edited use, −0.15 per rejected, clamped ±0.30); `reuse_stats(events, template_regions: dict[str, str], home_region: str = "US") -> dict` with keys `decisions, reused, rate, cross_region, cross_region_rate, rejections`.
- Decision payload contract: `{"template_ids": [str], "draft_event_id": int, "plan"?: dict, "reason"?: str}`.

- [ ] **Step 1: Write the failing test** `tests/test_learning.py`

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_learning.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.learning`)

- [ ] **Step 3: Implement** `revenueos/learning.py`

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_learning.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add revenueos/learning.py tests/test_learning.py
git commit -m "feat: ranking boosts and reuse stats derived from decision events"
```

---

### Task 5: Derived state

**Files:**
- Create: `revenueos/state.py`, `tests/test_state.py`

**Interfaces:**
- Consumes: `Event`, event constants; `business_days_between`, `health_for_age`, `GREEN/AMBER/RED`, `SLA_DAYS`; `reuse_stats`; `Template`.
- Produces: `BriefState` dataclass `(brief_id, account, owner, brief: dict, status, won_at, age_days, health, picked_up_at=None, draft: dict|None=None, draft_event_id: int|None=None)` where `status ∈ {"awaiting_draft","drafted","redraft","picked_up"}`; `derive_briefs(events, now) -> list[BriefState]`; `build_state(store, templates: dict[str, Template], now=None) -> dict` with keys `now, seam{health,open,green,amber,red,oldest_age_days,sla_days}, briefs[], reuse, triggers[], events[]`. Draft payload (from `DRAFT_GENERATED`): `{plan, handoff, candidates, mode, model}`.

- [ ] **Step 1: Write the failing test** `tests/test_state.py`

```python
from datetime import timedelta

from revenueos.events import (BRIEF_WON, CLOCK_ADVANCED, DECISION_ACCEPTED, DECISION_REJECTED,
                              DRAFT_GENERATED, TRIGGER_FIRED)
from revenueos.sla import shift_back_business_days
from revenueos.state import build_state, derive_briefs

BRIEF = {"account": "Acme", "region": "US", "segment": "insurer", "regulator": "NAIC",
         "problem": "p", "systems": [], "timeline": "", "success": ""}


def won(store, bid, age):
    ts = shift_back_business_days(store.now(), age)
    store.append(BRIEF_WON, "Priya", "Acme", bid, {"brief": BRIEF}, ts=ts)


def test_status_transitions(store):
    won(store, "b1", 0.5)
    assert derive_briefs(store.all(), store.now())[0].status == "awaiting_draft"
    store.append(DRAFT_GENERATED, "agent", "Acme", "b1", {"plan": {}, "mode": "fallback"})
    assert derive_briefs(store.all(), store.now())[0].status == "drafted"
    store.append(DECISION_REJECTED, "Marcus", "Acme", "b1", {"template_ids": []})
    assert derive_briefs(store.all(), store.now())[0].status == "redraft"
    store.append(DRAFT_GENERATED, "agent", "Acme", "b1", {"plan": {}, "mode": "fallback"})
    store.append(DECISION_ACCEPTED, "Marcus", "Acme", "b1", {"template_ids": []})
    b = derive_briefs(store.all(), store.now())[0]
    assert b.status == "picked_up" and b.picked_up_at is not None


def test_age_and_health_for_open_brief(store):
    won(store, "g", 0.5)
    won(store, "a", 1.5)
    won(store, "r", 3)
    by = {b.brief_id: b for b in derive_briefs(store.all(), store.now())}
    assert (by["g"].health, by["a"].health, by["r"].health) == ("green", "amber", "red")
    assert abs(by["r"].age_days - 3) < 0.01


def test_picked_up_age_is_frozen_at_pickup(store):
    won(store, "b1", 1.5)
    store.append(DRAFT_GENERATED, "agent", "Acme", "b1", {"plan": {}})
    store.append(DECISION_ACCEPTED, "Marcus", "Acme", "b1", {"template_ids": []})
    store.append(CLOCK_ADVANCED, "demo", "-", "-", {"hours": 24 * 10})
    b = derive_briefs(store.all(), store.now())[0]
    assert abs(b.age_days - 1.5) < 0.01 and b.health == "amber"


def test_build_state_seam_rollup_and_logs(store, templates):
    won(store, "g", 0.5)
    won(store, "r", 3)
    store.append(DRAFT_GENERATED, "agent", "Acme", "r", {"plan": {"objective": "x"}, "mode": "fallback"})
    store.append(TRIGGER_FIRED, "watcher", "Acme", "r", {"recipient": "Dana Whitfield"})
    st = build_state(store, {t.id: t for t in templates})
    assert st["seam"]["health"] == "red"
    assert (st["seam"]["green"], st["seam"]["amber"], st["seam"]["red"]) == (1, 0, 1)
    assert st["seam"]["open"] == 2 and st["seam"]["sla_days"] == 2.0
    assert st["briefs"][0]["brief_id"] == "r"  # oldest open brief first
    assert st["briefs"][0]["draft"]["plan"]["objective"] == "x"
    assert st["triggers"][0]["recipient"] == "Dana Whitfield"
    assert st["reuse"]["decisions"] == 0
    assert st["events"] and st["now"]


def test_empty_state_is_green(store, templates):
    st = build_state(store, {t.id: t for t in templates})
    assert st["seam"]["health"] == "green" and st["briefs"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_state.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.state`)

- [ ] **Step 3: Implement** `revenueos/state.py`

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_state.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add revenueos/state.py tests/test_state.py
git commit -m "feat: derive brief state, seam health and control-surface state from events"
```

---

### Task 6: CRM ingest (HubSpot-shaped)

**Files:**
- Create: `revenueos/ingest.py`, `tests/test_ingest.py`

**Interfaces:**
- Consumes: `Brief`, `EventStore`, `BRIEF_WON`.
- Produces: `make_hubspot_won_payload(*, object_id: str, rep: str, brief: Brief, occurred_at: datetime) -> dict`; `parse_hubspot_won(payload: dict) -> WonDeal` (dataclass `brief_id, owner, account, brief, occurred_at`; raises `ValueError` if not a `closedwon` deal-stage change or fields are missing); `ingest_won(store, payload) -> str | None` (returns `brief_id`, or `None` for a duplicate; appends `brief.won` with payload `{"brief": {...}, "deal": <raw payload>}`, owner = sales rep, ts = `occurredAt`).
- Production ownership note (stated in docs, not code): HubSpot owns the won-deal webhook; Gong/Fireflies owns the call content behind the brief.

- [ ] **Step 1: Write the failing test** `tests/test_ingest.py`

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.ingest`)

- [ ] **Step 3: Implement** `revenueos/ingest.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .events import BRIEF_WON, EventStore
from .models import Brief


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


def ingest_won(store: EventStore, payload: dict) -> str | None:
    deal = parse_hubspot_won(payload)
    if any(e.type == BRIEF_WON for e in store.for_brief(deal.brief_id)):
        return None  # HubSpot retries webhooks; ingest is idempotent
    store.append(BRIEF_WON, deal.owner, deal.account, deal.brief_id,
                 {"brief": deal.brief.model_dump(), "deal": payload}, ts=deal.occurred_at)
    return deal.brief_id
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_ingest.py -v`
Expected: 5 passed. Note: `occurredAt` is epoch milliseconds, so the round trip equals `now` only if `now` has no sub-millisecond part (the fixture does not).

- [ ] **Step 5: Commit**

```bash
git add revenueos/ingest.py tests/test_ingest.py
git commit -m "feat: HubSpot-shaped won-deal ingest as brief.won events"
```

---

### Task 7: Deterministic fallback drafter

**Files:**
- Create: `revenueos/agent/__init__.py` (empty), `revenueos/agent/fallback.py`, `tests/test_fallback.py`

**Interfaces:**
- Consumes: `Brief`, `Candidate`, `Template`, `PocPlan`, `HandoffDoc`, `DraftOutput`, `TemplateUse`, `WeekPlan`.
- Produces: `draft_fallback(brief: Brief, candidates: list[Candidate], templates: dict[str, Template]) -> DraftOutput`. Plan length = max effort of top-2 templates (+1 week if a template's regulator differs from the brief's). Handoff `missing_from_sales` always lists items a brief cannot contain (customer technical contact, sample data availability) plus any empty brief field.

- [ ] **Step 1: Write the failing test** `tests/test_fallback.py`

```python
from revenueos.agent.fallback import draft_fallback
from revenueos.models import Brief, DraftOutput
from revenueos.sample_briefs import SAMPLE_BRIEFS


def run(retriever, brief):
    cands = retriever.search(brief)
    return cands, draft_fallback(brief, cands, retriever.templates)


def test_returns_valid_schema_and_cites_templates(retriever):
    brief = SAMPLE_BRIEFS["northbridge"]
    cands, out = run(retriever, brief)
    assert isinstance(out, DraftOutput)
    used = [u.template_id for u in out.plan.templates_drawn_on]
    assert used and set(used) <= {c.template_id for c in cands}
    assert all(u.what_changes for u in out.plan.templates_drawn_on)


def test_plan_is_week_by_week_and_complete(retriever):
    _, out = run(retriever, SAMPLE_BRIEFS["northbridge"])
    weeks = out.plan.week_by_week
    assert [w.week for w in weeks] == list(range(1, len(weeks) + 1))
    assert weeks[-1].focus.startswith("Readout")
    for field in (out.plan.success_criteria, out.plan.scope_in, out.plan.scope_out,
                  out.plan.risks, out.plan.people_needed, out.plan.integrations):
        assert field


def test_cross_region_template_adds_a_week(retriever):
    brief = SAMPLE_BRIEFS["northbridge"]  # OCC; top template is FCA/RBI
    cands, out = run(retriever, brief)
    top2 = [retriever.templates[c.template_id] for c in cands[:2]]
    expected = max(t.effort_weeks for t in top2) + 1
    assert len(out.plan.week_by_week) == expected


def test_handoff_flags_what_sales_did_not_provide(retriever):
    brief = Brief(account="X", region="US", segment="insurer", regulator="NAIC",
                  problem="claims triage", systems=[], timeline="", success="")
    _, out = run(retriever, brief)
    missing = " ".join(out.handoff.missing_from_sales).lower()
    assert "timeline" in missing and "success" in missing and "systems" in missing
    assert "technical contact" in missing
    assert out.handoff.customer == "X"
    assert "NAIC" in out.handoff.regulatory_context


def test_works_with_no_candidates(retriever):
    out = draft_fallback(SAMPLE_BRIEFS["harbor"], [], retriever.templates)
    assert out.plan.templates_drawn_on == [] and len(out.plan.week_by_week) >= 5
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_fallback.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.agent`)

- [ ] **Step 3: Implement** `revenueos/agent/fallback.py`

```python
from __future__ import annotations

from ..models import (Brief, Candidate, DraftOutput, HandoffDoc, PocPlan, Template, TemplateUse,
                      WeekPlan)


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i.lower() not in seen:
            seen.add(i.lower())
            out.append(i)
    return out


def _week_plan(n: int, systems: list[str]) -> list[WeekPlan]:
    sys_txt = ", ".join(systems) or "customer systems"
    phases = [
        ("Discovery and template adaptation plan",
         ["Confirm scope and success criteria with the customer",
          "Pull the template repository and diff it against the brief"]),
        ("Adapt template and build core flow",
         ["Configure the reused capabilities", "Stand up the data pipeline on sample data"]),
        (f"Integrate with {sys_txt}",
         [f"Connect to {sys_txt} in the customer sandbox", "Run end-to-end on representative cases"]),
        ("Validate against success criteria",
         ["Measure against the agreed success criteria", "Review results with customer SMEs"]),
        ("Readout and handoff to Delivery",
         ["Present results and recommendation", "Complete the Delivery handoff document"]),
    ]
    out = []
    for w in range(1, n + 1):
        idx = 4 if w == n else min(3, (w - 1) * 5 // n)
        focus, acts = phases[idx]
        out.append(WeekPlan(week=w, focus=focus, activities=list(acts)))
    return out


def draft_fallback(brief: Brief, candidates: list[Candidate], templates: dict[str, Template]) -> DraftOutput:
    picks = [templates[c.template_id] for c in candidates[:2]]
    base = max((t.effort_weeks for t in picks), default=8)
    weeks = max(5, base + (1 if any(t.regulator != brief.regulator for t in picks) else 0))
    systems = ", ".join(brief.systems) or "the customer's systems"

    uses = []
    for t in picks:
        reg = (f"adapt controls from {t.regulator} to {brief.regulator}; "
               if t.regulator != brief.regulator else "")
        uses.append(TemplateUse(
            template_id=t.id,
            what_changes=f"Re-point integrations to {systems}; {reg}retune on {brief.account} data"))

    caps = _dedupe([c for t in picks for c in t.capabilities])
    integrations = _dedupe(brief.systems + [i for t in picks for i in t.integrations])
    success = [brief.success] if brief.success else []
    plan = PocPlan(
        objective=f"Prove within {weeks} weeks that the platform can: "
                  f"{brief.success or 'solve the stated problem'}",
        success_criteria=success + ["Results reviewed and accepted by customer subject-matter experts",
                                    f"Compliance review confirms alignment with {brief.regulator} expectations"],
        scope_in=caps[:5] or ["Core workflow described in the brief"],
        scope_out=["Production deployment and hardening", f"Integrations beyond {systems}",
                   "Model retraining beyond the POC dataset"],
        templates_drawn_on=uses,
        integrations=integrations,
        week_by_week=_week_plan(weeks, brief.systems),
        risks=[f"Customer data access under {brief.regulator} may delay week 1",
               "Template built for another region may encode different regulatory assumptions",
               f"Sandbox availability for {systems}"],
        people_needed=["Solution Architect (lead)", "Solutions Engineer", "Customer SME for acceptance",
                       "Delivery lead (from week 3)"],
    )

    missing = []
    if not brief.timeline:
        missing.append("Expected timeline")
    if not brief.success:
        missing.append("Success criteria for the POC")
    if not brief.systems:
        missing.append("Customer systems involved")
    if not brief.regulator:
        missing.append("Regulatory context")
    missing += ["Named customer technical contact and data owner",
                "Sample documents or data availability and access method"]

    handoff = HandoffDoc(
        customer=brief.account,
        regulatory_context=f"{brief.regulator} ({brief.segment}, {brief.region})",
        systems=brief.systems,
        commitments=[f"POC duration of {weeks} weeks", "Success criteria as listed in the POC plan"],
        open_questions=[f"Confirm API access and sandbox for {s}" for s in brief.systems]
                       + [f"Confirm data residency and retention rules under {brief.regulator}"],
        missing_from_sales=missing,
        delivery_prerequisites=[f"Environment and access requests aligned with {brief.regulator} controls",
                                f"Security review for integrations: {systems}"],
    )
    return DraftOutput(plan=plan, handoff=handoff)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_fallback.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add revenueos/agent tests/test_fallback.py
git commit -m "feat: deterministic POC plan and handoff drafter (fallback and eval baseline)"
```

---

### Task 8: LLM drafter

**Files:**
- Create: `revenueos/agent/llm.py`, `tests/fakes.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `Brief`, `Candidate`, `Template`, `DraftOutput`.
- Produces: `draft_llm(client, model: str, brief: Brief, candidates: list[Candidate], templates: dict[str, Template]) -> DraftOutput`. Calls `client.messages.create(model=..., max_tokens=4096, system=..., messages=[...], tools=[submit_draft], tool_choice={"type": "tool", "name": "submit_draft"})`, reads the `tool_use` block named `submit_draft`, validates `block.input` as `DraftOutput`. Raises `ValueError` (no tool block) or `pydantic.ValidationError` (bad shape).
- `tests/fakes.py` produces: `FakeClient(outputs)` where each output is a dict (returned as the tool input) or an Exception (raised); records `.messages.calls` and `.messages.last_kwargs`.

- [ ] **Step 1: Write the failing test** `tests/fakes.py` and `tests/test_llm.py`

`tests/fakes.py`:
```python
from types import SimpleNamespace


class FakeMessages:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        block = SimpleNamespace(type="tool_use", name="submit_draft", input=out)
        return SimpleNamespace(content=[block])


class FakeClient:
    def __init__(self, outputs):
        self.messages = FakeMessages(outputs)
```

`tests/test_llm.py`:
```python
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from revenueos.agent.fallback import draft_fallback
from revenueos.agent.llm import draft_llm
from revenueos.models import DraftOutput
from revenueos.sample_briefs import SAMPLE_BRIEFS
from tests.fakes import FakeClient


def good(retriever, brief):
    return draft_fallback(brief, retriever.search(brief), retriever.templates).model_dump()


def test_valid_tool_output_is_parsed(retriever):
    brief = SAMPLE_BRIEFS["northbridge"]
    client = FakeClient([good(retriever, brief)])
    out = draft_llm(client, "test-model", brief, retriever.search(brief), retriever.templates)
    assert isinstance(out, DraftOutput)
    kw = client.messages.last_kwargs
    assert kw["model"] == "test-model"
    assert kw["tool_choice"] == {"type": "tool", "name": "submit_draft"}
    assert kw["tools"][0]["name"] == "submit_draft"
    prompt = kw["messages"][0]["content"]
    assert "Northbridge Bank" in prompt and "TPL-" in prompt


def test_invalid_shape_raises_validation_error(retriever):
    brief = SAMPLE_BRIEFS["northbridge"]
    client = FakeClient([{"plan": {"objective": "only this"}, "handoff": {}}])
    with pytest.raises(ValidationError):
        draft_llm(client, "m", brief, retriever.search(brief), retriever.templates)


def test_missing_tool_block_raises_value_error(retriever):
    brief = SAMPLE_BRIEFS["northbridge"]

    class NoTool:
        class messages:
            @staticmethod
            def create(**kw):
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="hello")])

    with pytest.raises(ValueError):
        draft_llm(NoTool, "m", brief, retriever.search(brief), retriever.templates)
```
Also create empty `tests/__init__.py` so `from tests.fakes import ...` resolves.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.agent.llm`)

- [ ] **Step 3: Implement** `revenueos/agent/llm.py`

```python
from __future__ import annotations

import json

from ..models import Brief, Candidate, DraftOutput, Template

SYSTEM = """You are a pre-sales solutions agent at an enterprise software company that sells to banks and insurers.
You receive a won-opportunity brief and candidate solution templates retrieved from the template library.
Produce a POC plan and a handoff document skeleton that a solutions engineer can edit and send.

Rules:
- Reuse the candidate templates. Cite only template ids from the candidates list, and say concretely what changes for this customer (regulator, integrations, data).
- Do not invent customer facts. Anything the brief does not state goes into handoff.missing_from_sales or handoff.open_questions.
- week_by_week must cover the whole POC, one entry per week, ending with a readout and handoff to Delivery.
- Be specific and concise. No marketing language.
Call the submit_draft tool exactly once with the result."""

TOOL = {
    "name": "submit_draft",
    "description": "Submit the POC plan and the Delivery handoff document.",
    "input_schema": DraftOutput.model_json_schema(),
}


def _prompt(brief: Brief, candidates: list[Candidate], templates: dict[str, Template]) -> str:
    cand_blocks = []
    for c in candidates:
        t = templates[c.template_id]
        cand_blocks.append({"match": c.model_dump(), "template": t.model_dump()})
    return ("BRIEF\n" + json.dumps(brief.model_dump(), indent=2)
            + "\n\nCANDIDATE TEMPLATES (ranked, with why each matched)\n"
            + json.dumps(cand_blocks, indent=2))


def draft_llm(client, model: str, brief: Brief, candidates: list[Candidate],
              templates: dict[str, Template]) -> DraftOutput:
    resp = client.messages.create(
        model=model, max_tokens=4096, system=SYSTEM,
        messages=[{"role": "user", "content": _prompt(brief, candidates, templates)}],
        tools=[TOOL], tool_choice={"type": "tool", "name": "submit_draft"})
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_draft":
            return DraftOutput.model_validate(block.input)
    raise ValueError("model did not call submit_draft")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_llm.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add revenueos/agent/llm.py tests/fakes.py tests/__init__.py tests/test_llm.py
git commit -m "feat: Claude drafter with forced submit_draft tool and schema validation"
```

---

### Task 9: LangGraph agent flow

**Files:**
- Create: `revenueos/agent/graph.py`, `tests/test_graph.py`

**Interfaces:**
- Consumes: `EventStore` + event constants; `Retriever`; `template_boosts`; `draft_fallback`; `draft_llm`; models; `ARCHITECT` is not needed (owner arrives in the decision dict).
- Produces: `AgentDeps(store, retriever, llm_client=None, model="")`; `AgentRunner(deps, checkpointer=None)` with `start(brief_id) -> None` (reads the `brief.won` event, runs until the human interrupt; leaves a `draft.generated` event), `decide(brief_id, decision: dict) -> None` where `decision = {"action": "accept"|"edit"|"reject", "plan": dict|None, "reason": str, "owner": str}` (appends `decision.accepted|edited|rejected`; reject loops to a re-ranked redraft), `pending(brief_id) -> bool`.
- Event payloads written: `draft.generated` → `{plan, handoff, candidates, mode, model}`; `decision.*` → `{template_ids, draft_event_id, reason?, plan?}`.
- Graph: `retrieve → draft_llm → (ok→finalize | retry<2→draft_llm | else→draft_fallback→finalize) → await_decision(interrupt) → (reject→retrieve | else END)`.

- [ ] **Step 1: Write the failing test** `tests/test_graph.py`

```python
import pytest

from revenueos.agent.fallback import draft_fallback
from revenueos.agent.graph import AgentDeps, AgentRunner
from revenueos.events import (DECISION_ACCEPTED, DECISION_EDITED, DECISION_REJECTED,
                              DRAFT_GENERATED)
from revenueos.ingest import ingest_won, make_hubspot_won_payload
from revenueos.learning import template_boosts
from revenueos.sample_briefs import SAMPLE_BRIEFS
from tests.fakes import FakeClient


def runner(store, retriever, client=None):
    return AgentRunner(AgentDeps(store=store, retriever=retriever, llm_client=client, model="test"))


def seed(store, key="northbridge", oid="d1"):
    p = make_hubspot_won_payload(object_id=oid, rep="Priya Nair", brief=SAMPLE_BRIEFS[key],
                                 occurred_at=store.now())
    return ingest_won(store, p)


def of(store, typ):
    return [e for e in store.all() if e.type == typ]


def good(retriever, key="northbridge"):
    b = SAMPLE_BRIEFS[key]
    return draft_fallback(b, retriever.search(b), retriever.templates).model_dump()


def test_no_llm_drafts_with_fallback_then_waits_for_human(store, retriever):
    r = runner(store, retriever)
    bid = seed(store)
    r.start(bid)
    drafts = of(store, DRAFT_GENERATED)
    assert len(drafts) == 1 and drafts[0].payload["mode"] == "fallback"
    assert drafts[0].payload["candidates"] and drafts[0].payload["plan"]["templates_drawn_on"]
    assert r.pending(bid)


def test_llm_success_marks_mode_llm(store, retriever):
    client = FakeClient([good(retriever)])
    r = runner(store, retriever, client)
    r.start(seed(store))
    assert of(store, DRAFT_GENERATED)[0].payload["mode"] == "llm"
    assert client.messages.calls == 1


def test_llm_failing_twice_falls_back(store, retriever):
    client = FakeClient([RuntimeError("boom"), {"plan": {}, "handoff": {}}])
    r = runner(store, retriever, client)
    r.start(seed(store))
    assert of(store, DRAFT_GENERATED)[0].payload["mode"] == "fallback"
    assert client.messages.calls == 2


def test_llm_retry_recovers(store, retriever):
    client = FakeClient([RuntimeError("boom"), good(retriever)])
    r = runner(store, retriever, client)
    r.start(seed(store))
    assert of(store, DRAFT_GENERATED)[0].payload["mode"] == "llm"
    assert client.messages.calls == 2


def test_accept_records_decision_and_finishes(store, retriever):
    r = runner(store, retriever)
    bid = seed(store)
    r.start(bid)
    r.decide(bid, {"action": "accept", "plan": None, "reason": "", "owner": "Marcus Reyes"})
    d = of(store, DECISION_ACCEPTED)
    assert len(d) == 1 and d[0].owner == "Marcus Reyes" and d[0].payload["template_ids"]
    assert d[0].payload["draft_event_id"] == of(store, DRAFT_GENERATED)[0].id
    assert not r.pending(bid)
    assert all(v > 0 for v in template_boosts(store.all()).values())


def test_edit_uses_edited_plan_templates(store, retriever):
    r = runner(store, retriever)
    bid = seed(store)
    r.start(bid)
    plan = of(store, DRAFT_GENERATED)[0].payload["plan"]
    kept = plan["templates_drawn_on"][:1]
    plan = {**plan, "objective": "EDITED objective", "templates_drawn_on": kept}
    r.decide(bid, {"action": "edit", "plan": plan, "reason": "", "owner": "Marcus Reyes"})
    e = of(store, DECISION_EDITED)[0]
    assert e.payload["plan"]["objective"] == "EDITED objective"
    assert e.payload["template_ids"] == [kept[0]["template_id"]]


def test_reject_penalises_and_redrafts(store, retriever):
    r = runner(store, retriever)
    bid = seed(store)
    r.start(bid)
    first = of(store, DRAFT_GENERATED)[0]
    x = first.payload["plan"]["templates_drawn_on"][0]["template_id"]
    r.decide(bid, {"action": "reject", "plan": None, "reason": "wrong region", "owner": "Marcus Reyes"})
    assert len(of(store, DECISION_REJECTED)) == 1
    assert template_boosts(store.all())[x] == -0.15
    drafts = of(store, DRAFT_GENERATED)
    assert len(drafts) == 2 and r.pending(bid)
    s1 = {c["template_id"]: c["score"] for c in drafts[0].payload["candidates"]}
    s2 = {c["template_id"]: c["score"] for c in drafts[1].payload["candidates"]}
    assert s2.get(x, -1) < s1[x]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.agent.graph`)

- [ ] **Step 3: Implement** `revenueos/agent/graph.py`

```python
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..events import (BRIEF_WON, DECISION_ACCEPTED, DECISION_EDITED, DECISION_REJECTED,
                      DRAFT_GENERATED, EventStore)
from ..learning import template_boosts
from ..models import Brief, Candidate, DraftOutput, PocPlan
from ..retrieval import Retriever
from .fallback import draft_fallback
from .llm import draft_llm

AGENT = "poc-plan-agent"


@dataclass
class AgentDeps:
    store: EventStore
    retriever: Retriever
    llm_client: Any | None = None
    model: str = ""


class AgentState(TypedDict, total=False):
    brief_id: str
    account: str
    brief: dict
    candidates: list
    plan: dict | None
    handoff: dict | None
    mode: str
    model: str | None
    attempts: int
    error: str | None
    decision: dict
    draft_event_id: int


def build_graph(deps: AgentDeps, checkpointer=None):
    store, retriever = deps.store, deps.retriever

    def _parts(state):
        brief = Brief(**state["brief"])
        cands = [Candidate(**c) for c in state["candidates"]]
        return brief, cands

    def retrieve(state: AgentState):
        brief = Brief(**state["brief"])
        cands = retriever.search(brief, template_boosts(store.all()), k=3)
        return {"candidates": [c.model_dump() for c in cands], "plan": None, "handoff": None,
                "attempts": 0, "error": None}

    def draft_llm_node(state: AgentState):
        if deps.llm_client is None:
            return {"error": "no llm client configured", "attempts": 2}
        brief, cands = _parts(state)
        try:
            out = draft_llm(deps.llm_client, deps.model, brief, cands, retriever.templates)
        except Exception as e:  # validation errors, API errors, missing tool call
            return {"error": f"{type(e).__name__}: {e}", "attempts": state.get("attempts", 0) + 1}
        return {"plan": out.plan.model_dump(), "handoff": out.handoff.model_dump(),
                "mode": "llm", "model": deps.model, "error": None}

    def after_llm(state: AgentState):
        if state.get("error") is None:
            return "finalize"
        return "draft_llm" if state.get("attempts", 0) < 2 else "draft_fallback"

    def draft_fallback_node(state: AgentState):
        brief, cands = _parts(state)
        out: DraftOutput = draft_fallback(brief, cands, retriever.templates)
        return {"plan": out.plan.model_dump(), "handoff": out.handoff.model_dump(),
                "mode": "fallback", "model": None, "error": None}

    def finalize(state: AgentState):
        ev = store.append(DRAFT_GENERATED, AGENT, state["account"], state["brief_id"], {
            "plan": state["plan"], "handoff": state["handoff"], "candidates": state["candidates"],
            "mode": state["mode"], "model": state.get("model")})
        return {"draft_event_id": ev.id}

    def await_decision(state: AgentState):
        decision = interrupt({"brief_id": state["brief_id"], "draft_event_id": state["draft_event_id"]})
        action, owner = decision["action"], decision.get("owner", "unknown")
        payload = {"draft_event_id": state["draft_event_id"]}
        if action == "reject":
            payload["template_ids"] = [u["template_id"] for u in state["plan"]["templates_drawn_on"]]
            payload["reason"] = decision.get("reason", "")
            store.append(DECISION_REJECTED, owner, state["account"], state["brief_id"], payload)
        else:
            plan = PocPlan(**decision["plan"]).model_dump() if action == "edit" else state["plan"]
            payload["template_ids"] = [u["template_id"] for u in plan["templates_drawn_on"]]
            payload["plan"] = plan
            typ = DECISION_EDITED if action == "edit" else DECISION_ACCEPTED
            store.append(typ, owner, state["account"], state["brief_id"], payload)
        return {"decision": decision}

    def after_decision(state: AgentState):
        return "retrieve" if state["decision"]["action"] == "reject" else END

    g = StateGraph(AgentState)
    g.add_node("retrieve", retrieve)
    g.add_node("draft_llm", draft_llm_node)
    g.add_node("draft_fallback", draft_fallback_node)
    g.add_node("finalize", finalize)
    g.add_node("await_decision", await_decision)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "draft_llm")
    g.add_conditional_edges("draft_llm", after_llm, ["finalize", "draft_llm", "draft_fallback"])
    g.add_edge("draft_fallback", "finalize")
    g.add_edge("finalize", "await_decision")
    g.add_conditional_edges("await_decision", after_decision, ["retrieve", END])
    return g.compile(checkpointer=checkpointer or MemorySaver())


class AgentRunner:
    def __init__(self, deps: AgentDeps, checkpointer=None):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)
        self._lock = threading.Lock()

    @staticmethod
    def _cfg(brief_id: str) -> dict:
        return {"configurable": {"thread_id": brief_id}}

    def start(self, brief_id: str) -> None:
        won = next(e for e in self.deps.store.for_brief(brief_id) if e.type == BRIEF_WON)
        with self._lock:
            self.graph.invoke({"brief_id": brief_id, "account": won.account,
                               "brief": won.payload["brief"]}, self._cfg(brief_id))

    def decide(self, brief_id: str, decision: dict) -> None:
        with self._lock:
            self.graph.invoke(Command(resume=decision), self._cfg(brief_id))

    def pending(self, brief_id: str) -> bool:
        return bool(self.graph.get_state(self._cfg(brief_id)).next)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph.py -v`
Expected: 6 passed. If `AgentState` typing makes LangGraph reject `plan: dict | None` on this version, change those two annotations to `Any`. If an `Edit` of `decision["plan"]` fails validation, the exception surfaces from `decide`; the API validates beforehand (Task 11).

- [ ] **Step 5: Commit**

```bash
git add revenueos/agent/graph.py tests/test_graph.py
git commit -m "feat: LangGraph agent flow with human interrupt, retry, fallback and re-ranked redraft"
```

---

### Task 10: SLA watcher and triggers

**Files:**
- Create: `revenueos/config.py`, `revenueos/watcher.py`, `tests/test_watcher.py`

**Interfaces:**
- Consumes: `derive_briefs`, `AMBER_DAYS`, `SLA_DAYS`, event constants.
- Produces (`config.py`): `ARCHITECT="Marcus Reyes"`, `ESCALATION_OWNER="Dana Whitfield"`, `ESCALATION_ROLE="US PreSales Director"`, `ESCALATION_CHANNEL="#us-presales-escalations"`, `Settings` dataclass `(db_path="data/revenueos.db", anthropic_api_key="", model="claude-sonnet-5-5", agent_mode="auto", slack_url="", watcher_interval=5.0, seed_on_start=True, reset_on_start=True)`, `load_dotenv_file(path=".env")`, `from_env() -> Settings`.
- Produces (`watcher.py`): `tick(store, slack_url="", post=None) -> list[Event]` (idempotent; emits `sla.amber`, `sla.breached`, then `trigger.fired` with payload `{recipient, role, channel, message, draft_event_id, slack_payload, delivery}`; `delivery ∈ "logged" | "posted" | "failed: <err>"`); `async watch_forever(store, interval, slack_url="")`.

- [ ] **Step 1: Write the failing test** `tests/test_watcher.py`

```python
from revenueos.events import (BRIEF_WON, CLOCK_ADVANCED, DECISION_ACCEPTED, DRAFT_GENERATED,
                              SLA_AMBER, SLA_BREACHED, TRIGGER_FIRED)
from revenueos.sla import shift_back_business_days
from revenueos.watcher import tick

BRIEF = {"account": "Acme", "region": "US", "segment": "insurer", "regulator": "NAIC",
         "problem": "p", "systems": [], "timeline": "", "success": ""}


def won(store, bid, age):
    store.append(BRIEF_WON, "Priya", "Acme", bid, {"brief": BRIEF},
                 ts=shift_back_business_days(store.now(), age))


def types(events):
    return [e.type for e in events]


def test_green_brief_emits_nothing(store):
    won(store, "b1", 0.5)
    assert tick(store) == []


def test_amber_emitted_once(store):
    won(store, "b1", 1.5)
    assert types(tick(store)) == [SLA_AMBER]
    assert tick(store) == []


def test_breach_fires_trigger_with_draft_attached(store):
    won(store, "b1", 1.5)
    draft = store.append(DRAFT_GENERATED, "agent", "Acme", "b1",
                         {"plan": {"objective": "Prove X"}, "mode": "fallback"})
    tick(store)
    store.append(CLOCK_ADVANCED, "demo", "-", "-", {"hours": 24})
    out = tick(store)
    assert types(out) == [SLA_BREACHED, TRIGGER_FIRED]
    p = out[1].payload
    assert p["recipient"] == "Dana Whitfield" and p["role"] == "US PreSales Director"
    assert p["channel"] == "#us-presales-escalations"
    assert p["draft_event_id"] == draft.id and "Prove X" in p["slack_payload"]["attachments"][0]["text"]
    assert p["delivery"] == "logged"
    assert tick(store) == []  # idempotent


def test_deep_breach_emits_amber_too(store):
    won(store, "b1", 7)
    assert types(tick(store)) == [SLA_AMBER, SLA_BREACHED, TRIGGER_FIRED]


def test_breach_without_draft_still_fires(store):
    won(store, "b1", 3)
    trig = tick(store)[-1]
    assert trig.payload["draft_event_id"] is None and "no draft yet" in trig.payload["message"]


def test_picked_up_brief_is_ignored(store):
    won(store, "b1", 3)
    store.append(DECISION_ACCEPTED, "Marcus", "Acme", "b1", {"template_ids": []},
                 ts=shift_back_business_days(store.now(), 2.5))
    assert tick(store) == []


def test_slack_post_and_failure(store):
    won(store, "b1", 3)
    sent = []
    tick(store, slack_url="https://hooks.example/x", post=lambda url, body: sent.append((url, body)))
    assert sent and sent[0][0] == "https://hooks.example/x" and "text" in sent[0][1]

    s2 = store.__class__(real_now=store._real_now)
    won(s2, "b2", 3)

    def boom(url, body):
        raise OSError("network down")

    out = tick(s2, slack_url="https://hooks.example/x", post=boom)
    assert out[-1].payload["delivery"].startswith("failed: ")
```
Delivery status for the posted case is asserted via `sent`; also assert `delivery == "posted"` by reading the trigger event in the first scenario if desired.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_watcher.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.watcher`)

- [ ] **Step 3: Implement** `revenueos/config.py`

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ARCHITECT = "Marcus Reyes"
ESCALATION_OWNER = "Dana Whitfield"
ESCALATION_ROLE = "US PreSales Director"
ESCALATION_CHANNEL = "#us-presales-escalations"


@dataclass
class Settings:
    db_path: str = "data/revenueos.db"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-5-5"
    agent_mode: str = "auto"  # auto | fallback
    slack_url: str = ""
    watcher_interval: float = 5.0  # seconds; 0 disables the background watcher
    seed_on_start: bool = True
    reset_on_start: bool = True


def load_dotenv_file(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def from_env() -> Settings:
    load_dotenv_file()
    g = os.environ.get
    return Settings(
        db_path=g("REVENUEOS_DB", "data/revenueos.db"),
        anthropic_api_key=g("ANTHROPIC_API_KEY", ""),
        model=g("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        agent_mode=g("AGENT_MODE", "auto"),
        slack_url=g("SLACK_WEBHOOK_URL", ""),
        watcher_interval=float(g("WATCHER_INTERVAL", "5")),
    )
```

`revenueos/watcher.py`

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_watcher.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add revenueos/config.py revenueos/watcher.py tests/test_watcher.py
git commit -m "feat: SLA watcher fires idempotent amber/breach events and escalation triggers"
```

---

### Task 11: Seed data, API, demo controls

**Files:**
- Create: `revenueos/seed.py`, `revenueos/api.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces (`seed.py`): `SEED_PLAN = [("meridian","Priya Nair",0.5), ("northbridge","Priya Nair",1.5), ("atlas","Chris Alvarez",3.0), ("keystone","Chris Alvarez",7.0)]`; `seed(store, runner, slack_url="") -> list[str]` (backdates each brief by business days, ingests via the HubSpot path, runs the agent, then one watcher `tick`); `inject(store, runner) -> str` (new `harbor` brief aged 0, agent run, returns `brief_id`).
- Produces (`api.py`): `create_app(settings: Settings | None = None) -> FastAPI`. Routes: `GET /` (control surface), `POST /webhooks/crm`, `GET /api/state`, `POST /api/briefs/{brief_id}/decision` (body `{action, plan?, reason?}`; 404 unknown brief, 409 if not in `drafted`, 422 if `edit` has no plan), `POST /api/clock/advance` (body `{hours: 24}`, appends `clock.advanced`, runs one `tick`), `POST /api/demo/inject`, `POST /api/demo/reset`.
- Run: `uvicorn revenueos.api:create_app --factory --port 8000`.

- [ ] **Step 1: Write the failing test** `tests/test_api.py`

```python
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from revenueos.api import create_app
from revenueos.config import Settings
from revenueos.ingest import make_hubspot_won_payload
from revenueos.sample_briefs import SAMPLE_BRIEFS


@pytest.fixture
def client():
    s = Settings(db_path=":memory:", agent_mode="fallback", seed_on_start=False, watcher_interval=0)
    with TestClient(create_app(s)) as c:
        yield c


def post_won(client, oid="t1", key="northbridge"):
    p = make_hubspot_won_payload(object_id=oid, rep="Priya Nair", brief=SAMPLE_BRIEFS[key],
                                 occurred_at=datetime.now(timezone.utc))
    return client.post("/webhooks/crm", json=p)


def state(client):
    return client.get("/api/state").json()


def test_webhook_creates_brief_and_agent_drafts(client):
    r = post_won(client)
    assert r.json() == {"accepted": True, "brief_id": "brief-t1"}
    b = state(client)["briefs"][0]
    assert b["status"] == "drafted" and b["draft"]["mode"] == "fallback"
    assert b["draft"]["candidates"]


def test_webhook_ignores_duplicates_and_non_won(client):
    post_won(client)
    assert post_won(client).json()["ignored"] is True
    bad = make_hubspot_won_payload(object_id="x", rep="r", brief=SAMPLE_BRIEFS["atlas"],
                                   occurred_at=datetime.now(timezone.utc))
    bad["propertyValue"] = "appointmentscheduled"
    assert client.post("/webhooks/crm", json=bad).json()["ignored"] is True


def test_accept_picks_up_brief_and_moves_reuse(client):
    post_won(client)
    r = client.post("/api/briefs/brief-t1/decision", json={"action": "accept"})
    assert r.status_code == 200
    st = state(client)
    assert st["briefs"][0]["status"] == "picked_up"
    assert st["reuse"]["decisions"] == 1 and st["reuse"]["rate"] == 1.0
    assert st["seam"]["open"] == 0


def test_edit_requires_plan_and_stores_it(client):
    post_won(client)
    assert client.post("/api/briefs/brief-t1/decision", json={"action": "edit"}).status_code == 422
    plan = state(client)["briefs"][0]["draft"]["plan"]
    plan["objective"] = "My edited objective"
    r = client.post("/api/briefs/brief-t1/decision", json={"action": "edit", "plan": plan})
    assert r.status_code == 200
    assert state(client)["briefs"][0]["status"] == "picked_up"


def test_reject_redrafts_and_second_decision_still_allowed(client):
    post_won(client)
    client.post("/api/briefs/brief-t1/decision", json={"action": "reject", "reason": "wrong region"})
    b = state(client)["briefs"][0]
    assert b["status"] == "drafted"  # redrafted with new ranking
    assert client.post("/api/briefs/brief-t1/decision", json={"action": "accept"}).status_code == 200
    assert client.post("/api/briefs/brief-t1/decision", json={"action": "accept"}).status_code == 409


def test_unknown_brief_404(client):
    assert client.post("/api/briefs/nope/decision", json={"action": "accept"}).status_code == 404


def test_clock_advance_breaches_and_fires_trigger(client):
    post_won(client)
    assert state(client)["seam"]["health"] == "green"
    assert client.post("/api/clock/advance", json={"hours": 24 * 5}).status_code == 200
    st = state(client)
    assert st["seam"]["health"] == "red"
    assert st["triggers"] and st["triggers"][0]["recipient"] == "Dana Whitfield"
    assert st["triggers"][0]["draft_event_id"] == st["briefs"][0]["draft_event_id"]


def test_demo_inject_and_reset(client):
    assert client.post("/api/demo/inject").status_code == 200
    assert len(state(client)["briefs"]) == 1
    assert client.post("/api/demo/reset").status_code == 200
    assert state(client)["briefs"] == []


def test_seeded_app_shows_green_amber_red():
    s = Settings(db_path=":memory:", agent_mode="fallback", seed_on_start=True, watcher_interval=0)
    with TestClient(create_app(s)) as c:
        st = c.get("/api/state").json()
        assert {b["health"] for b in st["briefs"]} == {"green", "amber", "red"}
        assert st["triggers"]  # the 7- and 3-day-old briefs already escalated
        assert all(b["draft"] for b in st["briefs"])


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "RevenueOS" in r.text
```

Note: `test_index_served` needs `static/index.html` from Task 12. Create a one-line placeholder `revenueos/static/index.html` (`<title>RevenueOS</title>`) in this task so the test passes; Task 12 replaces it.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api.py -v`
Expected: FAIL (`ModuleNotFoundError: revenueos.api`)

- [ ] **Step 3: Implement** `revenueos/seed.py`

```python
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
```

`revenueos/api.py`

```python
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .agent.graph import AgentDeps, AgentRunner
from .config import ARCHITECT, Settings, from_env
from .events import CLOCK_ADVANCED, EventStore
from .ingest import ingest_won
from .library import load_templates
from .models import PocPlan
from .retrieval import Retriever
from .seed import inject, seed
from .state import build_state, derive_briefs
from .watcher import tick, watch_forever

STATIC = Path(__file__).parent / "static"


class Decision(BaseModel):
    action: Literal["accept", "edit", "reject"]
    plan: PocPlan | None = None
    reason: str = ""


class Advance(BaseModel):
    hours: float = 24.0


class Ctx:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.reset_on_start and settings.db_path != ":memory:" and os.path.exists(settings.db_path):
            os.remove(settings.db_path)
        self.store = EventStore(settings.db_path)
        self.templates = load_templates()
        self.retriever = Retriever(self.templates)
        self.runner = self._new_runner()

    def _new_runner(self) -> AgentRunner:
        client = None
        if self.settings.agent_mode == "auto" and self.settings.anthropic_api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return AgentRunner(AgentDeps(store=self.store, retriever=self.retriever,
                                     llm_client=client, model=self.settings.model))

    def state(self) -> dict:
        return build_state(self.store, self.retriever.templates)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()
    ctx = Ctx(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if settings.seed_on_start and not ctx.store.all():
            await asyncio.to_thread(seed, ctx.store, ctx.runner, settings.slack_url)
        if settings.watcher_interval > 0:
            task = asyncio.create_task(watch_forever(ctx.store, settings.watcher_interval, settings.slack_url))
        yield
        if task:
            task.cancel()

    app = FastAPI(title="RevenueOS", lifespan=lifespan)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/webhooks/crm")
    def crm_webhook(payload: dict, bg: BackgroundTasks):
        try:
            brief_id = ingest_won(ctx.store, payload)
        except ValueError as e:
            return {"ignored": True, "reason": str(e)}
        if brief_id is None:
            return {"ignored": True, "reason": "duplicate"}
        bg.add_task(ctx.runner.start, brief_id)
        return {"accepted": True, "brief_id": brief_id}

    @app.get("/api/state")
    def get_state():
        return ctx.state()

    @app.post("/api/briefs/{brief_id}/decision")
    def decide(brief_id: str, d: Decision):
        brief = next((b for b in derive_briefs(ctx.store.all(), ctx.store.now())
                      if b.brief_id == brief_id), None)
        if brief is None:
            raise HTTPException(404, "unknown brief")
        if brief.status != "drafted":
            raise HTTPException(409, f"brief is {brief.status}; nothing to decide")
        if d.action == "edit" and d.plan is None:
            raise HTTPException(422, "edit requires a plan")
        ctx.runner.decide(brief_id, {"action": d.action, "owner": ARCHITECT, "reason": d.reason,
                                     "plan": d.plan.model_dump() if d.plan else None})
        return ctx.state()

    @app.post("/api/clock/advance")
    def advance(a: Advance):
        ctx.store.append(CLOCK_ADVANCED, "demo-control", "-", "-", {"hours": a.hours})
        tick(ctx.store, settings.slack_url)
        return ctx.state()

    @app.post("/api/demo/inject")
    def demo_inject():
        return {"brief_id": inject(ctx.store, ctx.runner)}

    @app.post("/api/demo/reset")
    def demo_reset():
        ctx.store.reset()
        ctx.runner = ctx._new_runner()
        if settings.seed_on_start:
            seed(ctx.store, ctx.runner, settings.slack_url)
        return ctx.state()

    return app
```

Note: `test_demo_inject_and_reset` runs with `seed_on_start=False`, so reset leaves an empty state, as the test expects.

- [ ] **Step 4: Run to verify pass**

Run: `pytest -v`
Expected: the whole suite passes. In `test_reject_redrafts_and_second_decision_still_allowed`, the second accept after a successful first accept returns 409 because the brief is `picked_up`.

- [ ] **Step 5: Commit**

```bash
git add revenueos/seed.py revenueos/api.py revenueos/static tests/test_api.py
git commit -m "feat: FastAPI app, CRM webhook, decision/clock/demo endpoints, seeded briefs"
```

---

### Task 12: Control surface (one screen)

**Files:**
- Modify (replace placeholder): `revenueos/static/index.html`
- Test: `tests/test_api.py::test_index_served` (already exists) + manual checklist below

**Interfaces:**
- Consumes: `GET /api/state` (shape from Task 5), `POST /api/briefs/{id}/decision`, `POST /api/clock/advance`, `POST /api/demo/inject`, `POST /api/demo/reset`.
- Produces: single page with brief queue, seam health, current draft (editable), trigger log, actions.

- [ ] **Step 1: Replace `revenueos/static/index.html`** with:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RevenueOS - Brief to POC</title>
<style>
  :root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mute:#667085;--line:#e3e6ec;--green:#12805c;--amber:#b7791f;--red:#c0362c;--accent:#1f4fd8}
  @media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b24;--ink:#e8ebf2;--mute:#97a0b3;--line:#262c39;--green:#3ec28f;--amber:#e0a84a;--red:#ef6b61;--accent:#6c8cff}}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}
  header{display:flex;flex-wrap:wrap;gap:16px;align-items:center;padding:12px 20px;background:var(--card);border-bottom:1px solid var(--line)}
  h1{font-size:16px;margin:0}.pill{padding:3px 10px;border-radius:99px;color:#fff;font-weight:600;text-transform:uppercase;font-size:12px}
  .green{background:var(--green)}.amber{background:var(--amber)}.red{background:var(--red)}
  .stat{color:var(--mute)}.stat b{color:var(--ink)}
  main{display:grid;grid-template-columns:300px minmax(0,1fr) 340px;gap:16px;padding:16px 20px;align-items:start}
  @media (max-width:1100px){main{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
  .card h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mute);margin:0 0 8px}
  .brief{padding:10px;border:1px solid var(--line);border-radius:8px;margin-bottom:8px;cursor:pointer}
  .brief.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
  .brief .row{display:flex;justify-content:space-between;gap:8px;align-items:center}
  .mute{color:var(--mute)}.small{font-size:12px}
  label{display:block;font-size:12px;color:var(--mute);margin:10px 0 3px}
  textarea,input[type=text]{width:100%;font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
  textarea{min-height:64px;resize:vertical}
  .cand{padding:8px;border:1px dashed var(--line);border-radius:8px;margin-bottom:6px}
  .tpl{display:flex;gap:8px;align-items:center;margin-bottom:6px}.tpl input[type=text]{flex:1}
  .actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
  button{font:inherit;padding:7px 12px;border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.danger{color:var(--red);border-color:var(--red)}
  button:disabled{opacity:.5;cursor:not-allowed}
  .miss{color:var(--amber)}.ev{font-size:12px;border-bottom:1px solid var(--line);padding:4px 0}
  .trig{border-left:3px solid var(--red);padding:6px 8px;margin-bottom:8px;background:var(--bg);border-radius:4px}
  ul{margin:4px 0 0 18px;padding:0}
</style>
</head>
<body>
<header>
  <h1>RevenueOS &middot; Sales &rarr; PreSales seam (US)</h1>
  <span id="seam" class="pill green">green</span>
  <span class="stat" id="counts"></span>
  <span class="stat" id="oldest"></span>
  <span class="stat" id="reuse"></span>
  <span class="stat mute small">SLA 2 business days &middot; US baseline: 6.8 d, reuse 21% (UK 58%, India 55%)</span>
</header>
<main>
  <section class="card"><h2>Brief queue</h2><div id="queue"></div>
    <h2 style="margin-top:14px">Demo controls (synthetic)</h2>
    <div class="actions"><button id="adv">+24h clock</button><button id="inj">Inject brief</button><button id="rst" class="danger">Reset</button></div>
  </section>
  <section class="card" id="draft"><div class="mute">Select a brief.</div></section>
  <aside>
    <div class="card"><h2>Trigger log</h2><div id="triggers"></div></div>
    <div class="card" style="margin-top:16px"><h2>Recent OS events</h2><div id="events"></div></div>
  </aside>
</main>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let S = null, sel = null, drawnKey = null;

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} :
    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function refresh() { S = await api('/api/state'); render(); }

function render() {
  const s = S.seam;
  $('#seam').className = 'pill ' + s.health; $('#seam').textContent = s.health;
  $('#counts').innerHTML = `open <b>${s.open}</b> &middot; <span style="color:var(--green)">${s.green} green</span> &middot; <span style="color:var(--amber)">${s.amber} amber</span> &middot; <span style="color:var(--red)">${s.red} red</span>`;
  $('#oldest').innerHTML = `oldest <b>${s.oldest_age_days} d</b>`;
  const r = S.reuse;
  $('#reuse').innerHTML = `reuse <b>${Math.round(r.rate*100)}%</b> (${r.reused}/${r.decisions}) &middot; cross-region <b>${Math.round(r.cross_region_rate*100)}%</b> &middot; rejections <b>${r.rejections}</b>`;
  if (!sel && S.briefs.length) sel = S.briefs[0].brief_id;
  $('#queue').innerHTML = S.briefs.map(b => `
    <div class="brief ${b.brief_id===sel?'sel':''}" data-id="${esc(b.brief_id)}">
      <div class="row"><b>${esc(b.account)}</b><span class="pill ${b.health}">${b.age_days} d</span></div>
      <div class="small mute">${esc(b.brief.segment)} &middot; ${esc(b.brief.regulator)} &middot; ${esc(b.status.replace('_',' '))}</div>
    </div>`).join('') || '<div class="mute">No briefs.</div>';
  document.querySelectorAll('.brief').forEach(el => el.onclick = () => { sel = el.dataset.id; drawnKey = null; render(); });
  $('#triggers').innerHTML = S.triggers.map(t => `
    <div class="trig"><b>${esc(t.account)}</b> &rarr; ${esc(t.recipient)} (${esc(t.role)})<div class="small">${esc(t.message)}</div>
    <div class="small mute">${esc(t.channel)} &middot; delivery: ${esc(t.delivery)} &middot; draft #${esc(t.draft_event_id)}</div></div>`).join('') || '<div class="mute">No triggers fired.</div>';
  $('#events').innerHTML = S.events.slice(0, 14).map(e => `<div class="ev"><b>${esc(e.type)}</b> <span class="mute">${esc(e.account)}</span></div>`).join('');
  const b = S.briefs.find(x => x.brief_id === sel);
  const key = b ? `${b.brief_id}:${b.draft_event_id}:${b.status}` : 'none';
  if (key !== drawnKey) { drawnKey = key; renderDraft(b); }
}

const lines = a => (a || []).join('\n');
const parseLines = id => $(id).value.split('\n').map(x => x.trim()).filter(Boolean);

function renderDraft(b) {
  const el = $('#draft');
  if (!b) { el.innerHTML = '<div class="mute">Select a brief.</div>'; return; }
  if (!b.draft) { el.innerHTML = `<h2>${esc(b.account)}</h2><div class="mute">Agent is drafting...</div>`; return; }
  const d = b.draft, p = d.plan, h = d.handoff, done = b.status === 'picked_up', dis = done ? 'disabled' : '';
  el.innerHTML = `
    <h2>Draft for ${esc(b.account)} &middot; ${esc(d.mode)}${d.model ? ' (' + esc(d.model) + ')' : ' drafter'}</h2>
    <div class="small mute">${esc(b.brief.problem)}</div>
    <label>Candidate templates and why they matched</label>
    ${d.candidates.map(c => `<div class="cand"><b>${esc(c.name)}</b> <span class="mute small">${esc(c.template_id)} &middot; ${esc(c.region)} &middot; score ${c.score}</span>
      <ul class="small">${c.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>`).join('')}
    <label>Templates drawn on (untick to drop; edit what changes)</label>
    <div id="tpls">${p.templates_drawn_on.map(u => `<div class="tpl" data-id="${esc(u.template_id)}"><input type="checkbox" checked ${dis}><b>${esc(u.template_id)}</b><input type="text" value="${esc(u.what_changes)}" ${dis}></div>`).join('')}</div>
    <label>Objective</label><textarea id="p-objective" ${dis}>${esc(p.objective)}</textarea>
    <label>Success criteria (one per line)</label><textarea id="p-success" ${dis}>${esc(lines(p.success_criteria))}</textarea>
    <label>Scope in</label><textarea id="p-in" ${dis}>${esc(lines(p.scope_in))}</textarea>
    <label>Scope out</label><textarea id="p-out" ${dis}>${esc(lines(p.scope_out))}</textarea>
    <label>Integrations</label><textarea id="p-int" ${dis}>${esc(lines(p.integrations))}</textarea>
    <label>Week by week (one line per week: focus :: activity; activity)</label>
    <textarea id="p-weeks" style="min-height:150px" ${dis}>${esc(p.week_by_week.map(w => w.focus + ' :: ' + w.activities.join('; ')).join('\n'))}</textarea>
    <label>Risks</label><textarea id="p-risks" ${dis}>${esc(lines(p.risks))}</textarea>
    <label>People needed</label><textarea id="p-people" ${dis}>${esc(lines(p.people_needed))}</textarea>
    <h2 style="margin-top:18px">Delivery handoff skeleton</h2>
    <div class="small"><b>Customer:</b> ${esc(h.customer)} &middot; <b>Regulatory:</b> ${esc(h.regulatory_context)} &middot; <b>Systems:</b> ${esc(h.systems.join(', '))}</div>
    <label>Commitments</label><ul class="small">${h.commitments.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <label class="miss">Missing from Sales (ask before signing)</label><ul class="small miss">${h.missing_from_sales.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <label>Open questions</label><ul class="small">${h.open_questions.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <label>Delivery prerequisites</label><ul class="small">${h.delivery_prerequisites.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <div class="actions">
      ${done ? `<span class="mute">Decision recorded. Seam clock stopped at ${b.age_days} business days.</span>` : `
      <button class="primary" id="accept">Accept as drafted</button>
      <button id="edit">Save edits &amp; accept</button>
      <button class="danger" id="reject">Reject &amp; redraft</button>`}
      <button id="copy">Copy as Markdown</button>
    </div>
    <div id="err" class="small" style="color:var(--red);margin-top:8px"></div>`;
  if (!done) {
    $('#accept').onclick = () => decide(b, {action: 'accept'});
    $('#edit').onclick = () => decide(b, {action: 'edit', plan: collectPlan()});
    $('#reject').onclick = () => {
      const reason = prompt('Why reject? (the agent redrafts with new ranking)') ;
      if (reason !== null) decide(b, {action: 'reject', reason});
    };
  }
  $('#copy').onclick = () => navigator.clipboard.writeText(toMarkdown(b, collectPlan()));
}

function collectPlan() {
  const weeks = parseLines('#p-weeks').map((l, i) => {
    const [focus, acts = ''] = l.split('::');
    return {week: i + 1, focus: focus.trim(), activities: acts.split(';').map(x => x.trim()).filter(Boolean)};
  });
  const tpls = [...document.querySelectorAll('#tpls .tpl')].filter(r => r.querySelector('input[type=checkbox]').checked)
    .map(r => ({template_id: r.dataset.id, what_changes: r.querySelector('input[type=text]').value}));
  return {objective: $('#p-objective').value.trim(), success_criteria: parseLines('#p-success'),
    scope_in: parseLines('#p-in'), scope_out: parseLines('#p-out'), templates_drawn_on: tpls,
    integrations: parseLines('#p-int'), week_by_week: weeks, risks: parseLines('#p-risks'),
    people_needed: parseLines('#p-people')};
}

function toMarkdown(b, p) {
  const h = b.draft.handoff, ul = a => a.map(x => `- ${x}`).join('\n');
  return `# POC plan: ${b.account}\n\n**Objective:** ${p.objective}\n\n## Success criteria\n${ul(p.success_criteria)}\n\n## Scope in\n${ul(p.scope_in)}\n\n## Scope out\n${ul(p.scope_out)}\n\n## Templates drawn on\n${ul(p.templates_drawn_on.map(u => `${u.template_id}: ${u.what_changes}`))}\n\n## Integrations\n${ul(p.integrations)}\n\n## Plan\n${p.week_by_week.map(w => `**Week ${w.week}: ${w.focus}**\n${ul(w.activities)}`).join('\n\n')}\n\n## Risks\n${ul(p.risks)}\n\n## People\n${ul(p.people_needed)}\n\n# Delivery handoff\n**Customer:** ${h.customer}\n**Regulatory:** ${h.regulatory_context}\n\n## Commitments\n${ul(h.commitments)}\n\n## Missing from Sales\n${ul(h.missing_from_sales)}\n\n## Open questions\n${ul(h.open_questions)}\n\n## Delivery prerequisites\n${ul(h.delivery_prerequisites)}\n`;
}

async function decide(b, body) {
  try { S = await api(`/api/briefs/${encodeURIComponent(b.brief_id)}/decision`, body); drawnKey = null; render(); }
  catch (e) { $('#err').textContent = e.message; }
}
$('#adv').onclick = async () => { S = await api('/api/clock/advance', {hours: 24}); drawnKey = null; render(); };
$('#inj').onclick = async () => { const r = await api('/api/demo/inject', {}); sel = r.brief_id; await refresh(); };
$('#rst').onclick = async () => { sel = null; drawnKey = null; S = await api('/api/demo/reset', {}); render(); };
refresh(); setInterval(refresh, 2000);
</script>
</body>
</html>
```

- [ ] **Step 2: Run the suite**

Run: `pytest -v`
Expected: all passed (`test_index_served` checks the page contains "RevenueOS").

- [ ] **Step 3: Run the app and walk the manual checklist**

Run: `cp .env.example .env` (then put your key in `.env`; or set `AGENT_MODE=fallback`), then
`uvicorn revenueos.api:create_app --factory --port 8000` and open `http://localhost:8000`.
Check each item:
1. Header shows RED seam; queue shows 4 briefs aged 0.5 d (green), 1.5 d (amber), 3 d and 7 d (red).
2. Trigger log already shows escalations for the two red briefs, each naming Dana Whitfield with a draft number.
3. Select Northbridge Bank: candidate templates list UK/India KYC templates with reasons; plan, weeks and handoff are populated; "Missing from Sales" is highlighted.
4. Edit the objective, untick one template, click "Save edits & accept": brief leaves the open queue, reuse rate and counts change, `decision.edited` appears in OS events.
5. Select Atlas Credit, click "Reject & redraft" with a reason: a new draft appears and the rejected template's score is lower with a "learned boost -0.15" reason.
6. Inject brief (Harbor Trust Bank): appears green and drafted. Click "+24h clock" repeatedly until it turns amber then red and a new trigger appears in the log.
7. "Copy as Markdown" puts a pasteable plan and handoff on the clipboard.
8. Reset returns to the seeded state.
If a step fails, fix the code (not the checklist) and re-run.

- [ ] **Step 4: Commit**

```bash
git add revenueos/static/index.html
git commit -m "feat: single-screen control surface with queue, health, editable draft, trigger log"
```

---

### Task 13: Docs and deliverables

**Files:**
- Create: `README.md`, `docs/build-log.md`, `docs/cut-list.md`, `docs/never-hand-to-an-agent.md`, `docs/demo-script.md`

**Interfaces:**
- Consumes: the running system and the actual build history (commit log, any dead ends you hit).
- Produces: the four non-code deliverables from the challenge (section 9, items 8–10) plus run instructions.

- [ ] **Step 1: Write `README.md`** (fill with real values from your run)

Sections: what it is (3 lines); run it (`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env && uvicorn revenueos.api:create_app --factory --port 8000`); test it (`pytest`); stack and why (one paragraph: Python/FastAPI/SQLite/LangGraph rationale, no vector DB because 12 templates and explainability matter, plain Python for SLA because health is computed, not reported); **Declared synthetic** (briefs, templates, people, accounts, HubSpot payloads); integration view for production (HubSpot owns the won-deal webhook, Gong/Fireflies owns call content behind the brief, Slack owns trigger delivery, Notion or a database owns the template library, Clari would own pipeline-health roll-up); link to the spec, plan and `docs/diagrams/revenueos-architecture.html`.

- [ ] **Step 2: Write `docs/build-log.md`** (one page, from what actually happened)

Headings: Decomposition (the 13 tasks and why this order: events first because everything derives from them); Tools and models (Claude Code with the superpowers brainstorming → spec → plan → TDD flow, Archify for the architecture diagram, Claude as the runtime drafter); The instruction I iterated most (paste the final `SYSTEM` prompt from `revenueos/agent/llm.py` and the version history: what each revision changed); What failed first (record real failures, for example a ranking test that failed until template text was reworded, LangGraph/Python version issues, the first draft of the diagram having label collisions); What I threw away (for example the `search_templates` tool loop in favour of deterministic retrieval plus a forced `submit_draft`, a separate Regenerate button, SQLite checkpointer); Hours spent per task against the budget.

- [ ] **Step 3: Write `docs/cut-list.md`** (half a page)

Left out and why: real HubSpot/Slack/Gong integrations (synthetic data allowed), auth and roles, multi-region views, second seam, root-cause tagging, evaluation harness, vector retrieval, persistence of pending agent runs across restarts (app resets and re-seeds on boot). What breaks first at 10x volume, in order: (1) `os_events` full-table scans on every state build and every watcher tick, so add indexes and incremental projections or a materialised view; (2) in-memory BM25 over a growing library, so move to pgvector/Qdrant with hybrid search; (3) SQLite single-writer under concurrent webhooks, so move to Postgres; (4) single-process watcher and in-memory LangGraph checkpoints, so move to a durable queue and a Postgres checkpointer; (5) 2-second UI polling, so move to SSE or websockets; (6) LLM latency inside the request path, so make drafting fully async with status events.

- [ ] **Step 4: Write `docs/never-hand-to-an-agent.md`** (one paragraph)

State the one decision in this loop you would never delegate and why. Recommended position to argue (edit to your own view): committing the POC scope, timeline and success criteria to the customer. The agent can draft and flag gaps, but the commitment is a relationship and liability decision with asymmetric downside, depends on context no event captures (the buyer's politics, delivery capacity this quarter, what Sales promised verbally), and the accountability must sit with a named person. Ground it in the system: the agent's draft shows `missing_from_sales` precisely because it cannot know those things.

- [ ] **Step 5: Write `docs/demo-script.md`** (8 minutes or less)

Timeline: 0:00 problem and the 6.8-day seam (30 s) · 0:30 architecture diagram, one minute · 1:30 control surface tour: seeded red/amber/green and pre-fired triggers · 2:30 open Northbridge, show cross-region template reasons, edit and accept, watch reuse rate move · 4:30 reject Atlas, show the re-ranked redraft and the learned-boost reason · 5:30 inject Harbor, advance the clock to a live breach and show the trigger with draft attached · 6:30 show pytest passing and the event log as the source of truth · 7:15 one-paragraph "never hand to an agent" · stop by 8:00. Record with the real key set so the draft mode reads `llm`.

- [ ] **Step 6: Final verification and commit**

Run: `pytest -v` (all pass), restart the app and replay the demo script once end to end.

```bash
git add README.md docs
git commit -m "docs: README, build log, cut list, demo script, human-judgement paragraph"
```

---

## Self-Review (spec coverage)

| Spec item | Task |
|---|---|
| R1 event in (HubSpot-shaped, ≥3 briefs) | 3 (briefs), 6 (ingest), 11 (webhook, seed) |
| R2 agent: retrieve, explain, draft plan + handoff; ≥8 templates, ≥2 regions | 3, 7, 8, 9 |
| R3 person decides; decision is an event; reuse and ranking learn | 4, 9, 11, 12 |
| R4 seam watches itself; trigger with draft to named owner | 2, 5, 10 |
| R5 one control surface, can act from it | 12 |
| R6 built with AI, method shown | 13 (build log) |
| Deliverables 8–10 (build log, cut list, one paragraph) | 13 |
| Spec §4 event model / §5 SLA / §6 retrieval / §7 agent / §8 UI | 1,5,10 / 2,10 / 3,4 / 7,8,9 / 12 |
| Fallback when no key / LLM fails | 7, 9 (tests for retry and fallback) |
| Idempotent webhook and triggers | 6, 10 |

Type and name consistency checked across tasks: `derive_briefs`/`BriefState`/`build_state` (T5) are used by T10 and T11; `template_boosts`/`reuse_stats` (T4) by T5 and T9; `AgentRunner.start/decide/pending` (T9) by T11; decision payload keys `template_ids`, `draft_event_id` match between T4 and T9; `DraftOutput`, `PocPlan`, `TemplateUse` field names match between T1, T7, T8 and the UI in T12.

Stretch items (per-delay root-cause tags, second seam, regional variance, eval harness, real integrations) are deliberately not in this plan; if time remains after Task 13, the cheapest is the eval harness: run `draft_fallback` and `draft_llm` over the five sample briefs and score plan completeness.
