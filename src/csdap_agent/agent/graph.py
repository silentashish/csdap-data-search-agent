"""LangGraph orchestration around the Pydantic AI data-search agent.

Right now the graph is a single agent node, but it establishes the state
contract and wiring so branching (planner -> search -> verify -> respond)
can be added without touching callers.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic_ai.messages import ModelMessage

from ..csda.service import CsdaService, EarthdataCredentials
from .agent import AgentDeps, data_search_agent


class SearchState(TypedDict, total=False):
    """State threaded through the graph."""

    user_input: str
    earthdata: dict[str, str] | None  # {"username", "password"} or None
    history: list[ModelMessage]       # pydantic-ai message history
    answer: str
    messages: Annotated[list, add_messages]  # langchain-style channel (extensible)


async def _agent_node(state: SearchState) -> dict[str, Any]:
    creds = None
    ed = state.get("earthdata")
    if ed and ed.get("username") and ed.get("password"):
        creds = EarthdataCredentials(username=ed["username"], password=ed["password"])

    deps = AgentDeps(csda=CsdaService(credentials=creds))
    result = await data_search_agent.run(
        state["user_input"],
        deps=deps,
        message_history=state.get("history") or [],
    )
    return {"answer": result.output, "history": result.all_messages()}


def build_graph():
    graph = StateGraph(SearchState)
    graph.add_node("agent", _agent_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile()


# Compiled once; reused across requests.
search_graph = build_graph()
