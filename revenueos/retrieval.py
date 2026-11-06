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
