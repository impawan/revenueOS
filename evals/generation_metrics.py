import json

def get_assert(output, context):
    try:
        data = json.loads(output)
        plan = data.get("plan", {})
        handoff = data.get("handoff", {})
        candidates = data.get("retrieved_candidates", [])
        candidate_ids = {c.get("template_id") for c in candidates}
        
        errors = []
        
        # 1. Hallucination Check: templates drawn on must strictly come from retrieved candidates
        drawn_on = plan.get("templates_drawn_on", [])
        if not drawn_on:
            errors.append("No templates drawn on in the POC plan")
        for item in drawn_on:
            tid = item.get("template_id")
            if tid not in candidate_ids:
                errors.append(f"Hallucinated template cited: {tid} is not in retrieved candidates {candidate_ids}")
            if not item.get("what_changes"):
                errors.append(f"Template {tid} missing adaptation details ('what_changes')")

        # 2. Week-by-week completeness
        wbw = plan.get("week_by_week", [])
        if len(wbw) < 4:
            errors.append(f"POC week_by_week plan is too short ({len(wbw)} weeks)")
        last_week = wbw[-1] if wbw else {}
        last_focus = last_week.get("focus", "").lower() + " " + " ".join(last_week.get("activities", [])).lower()
        if "readout" not in last_focus and "handoff" not in last_focus and "delivery" not in last_focus:
            errors.append("Final week does not specify readout / delivery handoff")

        # 3. Grounding & Integrations coverage
        brief = data.get("brief", {})
        brief_systems = brief.get("systems", [])
        plan_integrations = plan.get("integrations", [])
        for sys in brief_systems:
            found = any(sys.lower() in pi.lower() or pi.lower() in sys.lower() for pi in plan_integrations)
            if not found:
                errors.append(f"Customer system '{sys}' not included in plan integrations: {plan_integrations}")

        # 4. Handoff sanity
        if not handoff.get("customer") or not handoff.get("regulatory_context"):
            errors.append("Handoff missing customer or regulatory_context")
        if not handoff.get("commitments"):
            errors.append("Handoff missing commitments")

        passed = len(errors) == 0
        return {
            "pass": passed,
            "score": 1.0 if passed else max(0.0, 1.0 - 0.25 * len(errors)),
            "reason": "; ".join(errors) if not passed else "Plan passed all RAG grounding, adaptation, and structure checks."
        }
    except Exception as e:
        return {"pass": False, "score": 0.0, "reason": f"Evaluation error: {str(e)}"}
