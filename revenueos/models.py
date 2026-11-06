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
