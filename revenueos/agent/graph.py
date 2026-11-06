from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..events import (BRIEF_WON, DECISION_ACCEPTED, DECISION_EDITED, DECISION_REJECTED,
                      DRAFT_GENERATED, EventStore)
from ..learning import template_boosts
from ..models import Brief, Candidate, DraftOutput, PocPlan
from ..retrieval import Retriever
from .fallback import draft_fallback
from .llm import draft_llm

AGENT = "poc-plan-agent"


@dataclass
class AgentDeps:
    store: EventStore
    retriever: Retriever
    llm_client: Any | None = None
    model: str = ""


class AgentState(TypedDict, total=False):
    brief_id: str
    account: str
    brief: dict
    candidates: list
    plan: dict | None
    handoff: dict | None
    mode: str
    model: str | None
    attempts: int
    error: str | None
    decision: dict
    draft_event_id: int
    reject_reason: str


def build_graph(deps: AgentDeps, checkpointer=None):
    store, retriever = deps.store, deps.retriever

    def _parts(state):
        brief = Brief(**state["brief"])
        cands = [Candidate(**c) for c in state["candidates"]]
        return brief, cands

    def retrieve(state: AgentState):
        brief = Brief(**state["brief"])
        cands = retriever.search(brief, template_boosts(store.all()), k=3)
        return {"candidates": [c.model_dump() for c in cands], "plan": None, "handoff": None,
                "attempts": 0, "error": None}

    def draft_llm_node(state: AgentState):
        if deps.llm_client is None:
            return {"error": "no llm client configured", "attempts": 2}
        brief, cands = _parts(state)
        try:
            out = draft_llm(deps.llm_client, deps.model, brief, cands, retriever.templates,
                            reject_reason=state.get("reject_reason", ""))
        except Exception as e:  # validation errors, API errors, missing tool call
            return {"error": f"{type(e).__name__}: {e}", "attempts": state.get("attempts", 0) + 1}
        return {"plan": out.plan.model_dump(), "handoff": out.handoff.model_dump(),
                "mode": "llm", "model": deps.model, "error": None}

    def after_llm(state: AgentState):
        if state.get("error") is None:
            return "finalize"
        return "draft_llm" if state.get("attempts", 0) < 2 else "draft_fallback"

    def draft_fallback_node(state: AgentState):
        brief, cands = _parts(state)
        out: DraftOutput = draft_fallback(brief, cands, retriever.templates,
                                          reject_reason=state.get("reject_reason", ""))
        return {"plan": out.plan.model_dump(), "handoff": out.handoff.model_dump(),
                "mode": "fallback", "model": None, "error": None}

    def finalize(state: AgentState):
        ev = store.append(DRAFT_GENERATED, AGENT, state["account"], state["brief_id"], {
            "plan": state["plan"], "handoff": state["handoff"], "candidates": state["candidates"],
            "mode": state["mode"], "model": state.get("model")})
        return {"draft_event_id": ev.id}

    def await_decision(state: AgentState):
        decision = interrupt({"brief_id": state["brief_id"], "draft_event_id": state["draft_event_id"]})
        action, owner = decision["action"], decision.get("owner", "unknown")
        payload = {"draft_event_id": state["draft_event_id"]}
        if action == "reject":
            payload["template_ids"] = [u["template_id"] for u in state["plan"]["templates_drawn_on"]]
            payload["reason"] = decision.get("reason", "")
            store.append(DECISION_REJECTED, owner, state["account"], state["brief_id"], payload)
        else:
            plan = PocPlan(**decision["plan"]).model_dump() if action == "edit" else state["plan"]
            payload["template_ids"] = [u["template_id"] for u in plan["templates_drawn_on"]]
            payload["plan"] = plan
            typ = DECISION_EDITED if action == "edit" else DECISION_ACCEPTED
            store.append(typ, owner, state["account"], state["brief_id"], payload)
        return {"decision": decision, "reject_reason": decision.get("reason", "") if action == "reject" else ""}

    def after_decision(state: AgentState):
        return "retrieve" if state["decision"]["action"] == "reject" else END

    g = StateGraph(AgentState)
    g.add_node("retrieve", retrieve)
    g.add_node("draft_llm", draft_llm_node)
    g.add_node("draft_fallback", draft_fallback_node)
    g.add_node("finalize", finalize)
    g.add_node("await_decision", await_decision)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "draft_llm")
    g.add_conditional_edges("draft_llm", after_llm, ["finalize", "draft_llm", "draft_fallback"])
    g.add_edge("draft_fallback", "finalize")
    g.add_edge("finalize", "await_decision")
    g.add_conditional_edges("await_decision", after_decision, ["retrieve", END])
    return g.compile(checkpointer=checkpointer or MemorySaver())


class AgentRunner:
    def __init__(self, deps: AgentDeps, checkpointer=None):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)
        self._lock = threading.Lock()

    @staticmethod
    def _cfg(brief_id: str) -> dict:
        return {"configurable": {"thread_id": brief_id}}

    def start(self, brief_id: str) -> None:
        won = next(e for e in self.deps.store.for_brief(brief_id) if e.type == BRIEF_WON)
        with self._lock:
            self.graph.invoke({"brief_id": brief_id, "account": won.account,
                               "brief": won.payload["brief"]}, self._cfg(brief_id))

    def decide(self, brief_id: str, decision: dict, guard=None) -> None:
        with self._lock:
            if guard is not None:
                guard()  # validated under the lock so concurrent stale decisions cannot slip through
            self.graph.invoke(Command(resume=decision), self._cfg(brief_id))

    def pending(self, brief_id: str) -> bool:
        return bool(self.graph.get_state(self._cfg(brief_id)).next)
