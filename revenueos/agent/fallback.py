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


def draft_fallback(brief: Brief, candidates: list[Candidate], templates: dict[str, Template],
                   reject_reason: str = "") -> DraftOutput:
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

    reason = reject_reason.strip()
    if reason:
        plan.risks.insert(0, f'Previous draft was rejected by the architect: "{reason}". '
                             "This redraft must be checked against that concern.")
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
                       + ([f'Does this redraft resolve the rejection reason: "{reason}"?'] if reason else [])
                       + [f"Confirm data residency and retention rules under {brief.regulator}"],
        missing_from_sales=missing,
        delivery_prerequisites=[f"Environment and access requests aligned with {brief.regulator} controls",
                                f"Security review for integrations: {systems}"],
    )
    return DraftOutput(plan=plan, handoff=handoff)
