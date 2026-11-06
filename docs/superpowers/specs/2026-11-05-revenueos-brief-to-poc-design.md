# RevenueOS Brief-to-POC-Plan Loop: Design

Date: 2026-11-05 · Time-box: 8 hours · Status: draft for review

## 1. Goal

Clear the Sales→PreSales handoff (US SLA: 2 business days, actual 6.8). A won deal arrives as an event, an agent retrieves reusable templates and drafts an editable POC plan plus a Delivery handoff skeleton, a US Solution Architect decides, the decision trains ranking, and the seam computes its own health and escalates on breach. Covers R1–R6 of the challenge.

## 2. Stack (decided)

Python 3.12+/3.14 · FastAPI + Uvicorn · SQLite (stdlib) · LangGraph (agent flow only, `interrupt` for the human gate, in-memory checkpointer; the app resets and re-seeds on boot, so pending interrupts never outlive the process) · Anthropic SDK called directly inside the draft node (model from `ANTHROPIC_MODEL`, default `claude-sonnet-5-5`) · Pydantic v2 · BM25 + filters + learned boost (no vector DB, no embeddings) · one static HTML page, vanilla JS · pytest.
First task verifies LangGraph installs on the local Python; if not, install Python 3.12/3.13 and use that.

## 3. Architecture

See `docs/diagrams/revenueos-architecture.html`. Components: CRM mock → FastAPI webhook → `os_events` (append-only, source of truth) → agent → control surface; SLA watcher reads events and fires triggers; architect decisions write events and feed library ranking.

Rules:
- `os_events` is the only written store of facts. Queue, health, reuse rate and ranking boosts are derived by reading events.
- SLA, triggers, ingest and ranking updates are plain deterministic Python. Only draft generation uses an LLM.

## 4. Event model

Table `os_events(id, ts, type, owner, account, brief_id, payload_json)`; `ts` is UTC ISO-8601.

| type | emitted by | payload highlights |
|---|---|---|
| `brief.won` | CRM webhook | brief (account, region, segment, regulator, problem, systems, timeline, success criteria), HubSpot-shaped deal envelope |
| `draft.generated` | agent | plan, handoff doc, candidates with match reasons, `mode` (`llm`\|`fallback`), model |
| `decision.accepted` / `decision.edited` / `decision.rejected` | architect | final plan, template ids used, reject reason |
| `sla.amber` / `sla.breached` | SLA watcher | age in business days, threshold |
| `trigger.fired` | SLA watcher | recipient, channel, message, draft attached (draft event id) |
| `clock.advanced` | demo control | hours advanced (demo clock only) |

Derived brief state: `awaiting_draft → drafted → picked_up` (accepted/edited) or `redraft` (rejected). The seam clock starts at `brief.won.ts` and stops at the first accepted or edited decision. A rejection keeps the clock running and requests a redraft.

Webhook payload mimics a HubSpot `deal.propertyChange` (dealstage → `closedwon`) with `objectId`, `occurredAt`, `portalId`, and a brief carried in custom properties. Stated production ownership: HubSpot owns the won-deal event, Gong/Fireflies the call content behind the brief, Slack the triggers, Notion or a database the template library.

## 5. SLA and triggers

- Age = business days (Mon–Fri) between `brief.won.ts` and (decision ts | now), where now = real time + sum of `clock.advanced`.
- Green < 1.0 days, amber 1.0 to < 2.0, red ≥ 2.0 (breach). Seam health = worst brief state, plus counts.
- Watcher loop runs every 5 s. On first crossing of each threshold it writes `sla.amber` or `sla.breached` once. On breach it also writes `trigger.fired` to the named owner with the current draft attached and POSTs a Slack-format payload only if `SLACK_WEBHOOK_URL` is set. Idempotent per brief per threshold.
- Named owners (synthetic): US Solution Architect Marcus Reyes (decider), US PreSales Director Dana Whitfield (breach escalation).

## 6. Template library and retrieval

12 synthetic templates across US, UK, India, Middle East: KYC document review, loan underwriting assist, claims triage, regulatory reporting, customer-service copilot, fraud alert triage, and variants. Fields follow the challenge's suggested shape. Stored as JSON, loaded to memory.

Score = 0.6 · normalized BM25(brief text vs template text) + 0.3 · filter match (segment 0.5, regulator 0.3, integrations overlap 0.2) + learned boost. Boost = clamp(+0.10 per accept/edit, −0.15 per reject, ±0.30), keyed by template, recomputed from decision events. Every candidate carries machine-built match reasons (shared terms, matched filters, boost) that the agent turns into prose. Reuse rate = decisions that drew on at least one template ÷ all accept/edit decisions. A region-of-origin split shows cross-region reuse, the headline fix.

## 7. Agent (LangGraph)

State: brief, candidates, draft, attempts, mode.
Nodes: `retrieve` → `draft_llm` → `validate` → (`ok` → `await_decision` interrupt | `bad` → retry once → `draft_fallback`) → record `draft.generated`. On resume the decision becomes an event.
`retrieve` is a deterministic node (BM25 + filters + boost). `draft_llm` gives Claude the brief and the retrieved candidates in one call and forces a single `submit_draft` tool call whose input schema is `DraftOutput` (`PocPlan` + `HandoffDoc`), validated by Pydantic. A rejection loops back to `retrieve`, so the new boost changes the redraft.
- `PocPlan`: objective, success_criteria, scope_in/out, templates_drawn_on with what changes, integrations, week_by_week, risks, people_needed.
- `HandoffDoc`: customer, regulatory context, systems, commitments, open questions, items still needed from Sales (feeds the 94% completeness metric), Delivery prerequisites.
`draft_fallback`: deterministic fill of the same schema from top-ranked templates. Used when there is no key, the call fails, or validation fails twice. The mode is shown on screen.

## 8. Control surface (one page)

Left: brief queue with age and G/A/R chips. Top: seam health (counts, oldest age, reuse rate, SLA target). Centre: current draft, editable fields, matched templates with reasons. Actions: Accept, Save edits & accept, Reject & redraft (with reason; the agent re-ranks and redrafts). Right: trigger log and recent events. Demo controls: Advance clock (+1 business day), Inject new brief, Reset. Updates by polling `/api/state` every 2 s.

## 9. Data declared synthetic

All briefs, templates, people, accounts and the HubSpot payloads. Seeded briefs: KYC (insurer, 0.5 d), claims triage (insurer, 1.5 d), fraud triage (retail bank, 3 d, already breached), regulatory reporting (capital markets, 7 d), plus one injectable.

## 10. Testing

pytest on: business-day math, threshold transitions and idempotent triggers, retrieval ranking and filters, boost learning from decisions, event-derived state, schema validation and fallback, webhook ingest. Agent tests run against fallback and a stubbed LLM. A small eval script (stretch) scores plan completeness for LLM vs fallback.

## 11. Out of scope

Real integrations, auth, multi-region UI, second seam, vector retrieval, concurrent writers. (Cut list will cover what breaks at 10x: SQLite writers, in-memory BM25, polling, single-process watcher.)

## 12. Deliverables mapping

Running system (README run steps), repo with commit history (needs `git init`), build log, cut list, "never hand to an agent" paragraph in `docs/`.
