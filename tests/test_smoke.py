def test_langgraph_interrupt_roundtrip():
    from typing import TypedDict
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    class S(TypedDict, total=False):
        answer: str

    def ask(state):
        return {"answer": interrupt("need input")}

    g = StateGraph(S)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    graph.invoke({}, cfg)
    assert graph.get_state(cfg).next == ("ask",)
    out = graph.invoke(Command(resume="yes"), cfg)
    assert out["answer"] == "yes"
