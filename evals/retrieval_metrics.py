import json

def get_assert(output, context):
    try:
        data = json.loads(output)
        vars_dict = context.get('vars', {})
        
        expected_top = vars_dict.get('expected_top')
        expected_in_top_k = vars_dict.get('expected_in_top_k', [])
        if isinstance(expected_in_top_k, str):
            expected_in_top_k = [expected_in_top_k]
        
        retrieved_ids = data.get('retrieved_ids', [])
        top_id = data.get('top_template_id')
        
        reasons = []
        passed = True
        
        if expected_top and top_id != expected_top:
            passed = False
            reasons.append(f"Expected top template '{expected_top}', but got '{top_id}'")
            
        for exp_id in expected_in_top_k:
            if exp_id not in retrieved_ids:
                passed = False
                reasons.append(f"Expected template '{exp_id}' in top-k results {retrieved_ids}")
                
        # Validate match reasoning completeness
        candidates = data.get('candidates', [])
        for c in candidates:
            if not c.get('reasons'):
                passed = False
                reasons.append(f"Template {c.get('template_id')} is missing match explainability reasons")
                
        return {
            "pass": passed,
            "score": 1.0 if passed else 0.0,
            "reason": "; ".join(reasons) if not passed else f"Retrieval accurate! Top: {top_id}, Retrieved: {retrieved_ids}"
        }
    except Exception as e:
        return {"pass": False, "score": 0.0, "reason": f"Evaluation error: {str(e)}"}
