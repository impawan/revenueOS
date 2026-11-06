import json
import os
from revenueos.library import load_templates
from revenueos.retrieval import Retriever
from revenueos.models import Brief
from revenueos.agent.fallback import draft_fallback
from revenueos.agent.llm import draft_llm

templates_list = load_templates()
templates_dict = {t.id: t for t in templates_list}
retriever = Retriever(templates_list)

def call_api(prompt, options, context):
    try:
        vars_dict = context.get('vars', {})
        brief_data = vars_dict.get('brief')
        if not brief_data:
            brief_str = vars_dict.get('brief_json')
            if brief_str:
                brief_data = json.loads(brief_str)
            else:
                return {"error": "Missing 'brief' or 'brief_json' in test vars"}

        brief = Brief(**brief_data)
        boosts = vars_dict.get('boosts', {})
        candidates = retriever.search(brief, boosts=boosts, k=3)

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        model = vars_dict.get("model", os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
        reject_reason = vars_dict.get("reject_reason", "")

        draft_output = None
        mode = "fallback"

        if api_key:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                draft_output = draft_llm(client, model, brief, candidates, templates_dict, reject_reason)
                mode = "llm"
            except Exception:
                draft_output = draft_fallback(brief, candidates, templates_dict)
                mode = "fallback_on_error"
        else:
            draft_output = draft_fallback(brief, candidates, templates_dict)
            mode = "fallback"

        payload = {
            "mode": mode,
            "brief": brief.model_dump(),
            "retrieved_candidates": [c.model_dump() for c in candidates],
            "plan": draft_output.plan.model_dump(),
            "handoff": draft_output.handoff.model_dump()
        }

        return {
            "output": json.dumps(payload, indent=2)
        }
    except Exception as e:
        return {"error": str(e)}
