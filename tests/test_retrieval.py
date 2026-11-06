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
