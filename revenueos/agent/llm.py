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


def _prompt(brief: Brief, candidates: list[Candidate], templates: dict[str, Template],
            reject_reason: str = "") -> str:
    cand_blocks = []
    for c in candidates:
        t = templates[c.template_id]
        cand_blocks.append({"match": c.model_dump(), "template": t.model_dump()})
    redraft = (f"\n\nPREVIOUS DRAFT WAS REJECTED BY THE ARCHITECT. Reason: {reject_reason}\n"
               "Address this reason in the new plan and say how in plan.risks or handoff.open_questions."
               if reject_reason else "")
    return ("BRIEF\n" + json.dumps(brief.model_dump(), indent=2)
            + "\n\nCANDIDATE TEMPLATES (ranked, with why each matched)\n"
            + json.dumps(cand_blocks, indent=2) + redraft)


def draft_llm(client, model: str, brief: Brief, candidates: list[Candidate],
              templates: dict[str, Template], reject_reason: str = "") -> DraftOutput:
    resp = client.messages.create(
        model=model, max_tokens=4096, system=SYSTEM,
        messages=[{"role": "user", "content": _prompt(brief, candidates, templates, reject_reason)}],
        tools=[TOOL], tool_choice={"type": "tool", "name": "submit_draft"})
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_draft":
            out = DraftOutput.model_validate(block.input)
            allowed = {c.template_id for c in candidates}
            bad = [u.template_id for u in out.plan.templates_drawn_on if u.template_id not in allowed]
            if bad:  # fails into the graph's retry/fallback path
                raise ValueError(f"model cited template ids that are not candidates: {bad}")
            return out
    raise ValueError("model did not call submit_draft")
