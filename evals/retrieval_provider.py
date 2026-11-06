import json
import sys
from revenueos.library import load_templates
from revenueos.retrieval import Retriever
from revenueos.models import Brief

# Load retriever once
templates = load_templates()
retriever = Retriever(templates)

def call_api(prompt, options, context):
    try:
        # Promptfoo passes the test case variables in context.get('vars', {})
        vars_dict = context.get('vars', {})
        brief_data = vars_dict.get('brief')
        if not brief_data:
            # Maybe passed as json string
            brief_str = vars_dict.get('brief_json')
            if brief_str:
                brief_data = json.loads(brief_str)
            else:
                return {"error": "Missing 'brief' or 'brief_json' in test variables"}
        
        brief = Brief(**brief_data)
        boosts = vars_dict.get('boosts', {})
        k = int(vars_dict.get('top_k', 3))

        candidates = retriever.search(brief, boosts=boosts, k=k)
        
        retrieved_templates = []
        for c in candidates:
            t = templates.get(c.template_id) if isinstance(templates, dict) else next((x for x in templates if x.id == c.template_id), None)
            retrieved_templates.append({
                "template_id": c.template_id,
                "score": round(c.score, 4),
                "reasons": c.reasons,
                "name": t.name if t else "",
                "segment": t.segment if t else "",
                "regulator": t.regulator if t else "",
                "integrations": t.integrations if t else []
            })

        output_payload = {
            "query_account": brief.account,
            "query_segment": brief.segment,
            "query_regulator": brief.regulator,
            "query_systems": brief.systems,
            "retrieved_ids": [c.template_id for c in candidates],
            "top_template_id": candidates[0].template_id if candidates else None,
            "candidates": retrieved_templates
        }

        return {
            "output": json.dumps(output_payload, indent=2)
        }
    except Exception as e:
        return {"error": str(e)}
