"""LangGraph orchestration around the Pydantic AI data-search agent.

The graph is a single agent node today, but it establishes the state contract
and wiring so branching (planner -> search -> verify -> respond) can be added
without touching callers.

The agent node runs Pydantic AI in *streaming* mode and emits UI-agnostic
events (thinking / answer tokens, tool calls, tool results) through an optional
async callback carried on the state. The Chainlit layer turns those into live
steps so the user can see what the LLM is doing.
"""

from __future__ import annotations

from typing import Annotated, Any, Awaitable, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)

from ..csda.service import CsdaService, EarthdataCredentials
from .agent import AgentDeps, data_search_agent

# (kind, payload) -> None. kinds: "thinking", "answer", "tool_call", "tool_result".
EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class SearchState(TypedDict, total=False):
    """State threaded through the graph."""

    user_input: str
    earthdata: dict[str, str] | None       # {"username", "password"} or None
    history: list[ModelMessage]            # pydantic-ai message history
    on_event: Optional[EventCallback]      # UI event sink (never persisted)
    thread_id: str                         # Chainlit thread id (panel state key)
    answer: str
    messages: Annotated[list, add_messages]  # langchain-style channel (extensible)


async def _agent_node(state: SearchState) -> dict[str, Any]:
    creds = None
    ed = state.get("earthdata")
    if ed and ed.get("username") and ed.get("password"):
        creds = EarthdataCredentials(username=ed["username"], password=ed["password"])

    deps = AgentDeps(
        csda=CsdaService(credentials=creds),
        thread_id=state.get("thread_id") or "default",
    )
    on_event = state.get("on_event")

    async def emit(kind: str, payload: dict[str, Any]) -> None:
        if on_event is not None:
            await on_event(kind, payload)

    async with data_search_agent.iter(
        state["user_input"],
        deps=deps,
        message_history=state.get("history") or [],
    ) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                # Model is producing reasoning ("thinking") and/or answer text.
                async with node.stream(run.ctx) as request_stream:
                    async for event in request_stream:
                        if isinstance(event, PartStartEvent):
                            part = event.part
                            if isinstance(part, ThinkingPart) and part.content:
                                await emit("thinking", {"delta": part.content})
                            elif isinstance(part, TextPart) and part.content:
                                await emit("answer", {"delta": part.content})
                        elif isinstance(event, PartDeltaEvent):
                            delta = event.delta
                            if isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                                await emit("thinking", {"delta": delta.content_delta})
                            elif isinstance(delta, TextPartDelta) and delta.content_delta:
                                await emit("answer", {"delta": delta.content_delta})
            elif Agent.is_call_tools_node(node):
                # Model decided to call tools; surface each call and its result.
                async with node.stream(run.ctx) as tool_stream:
                    async for event in tool_stream:
                        if isinstance(event, FunctionToolCallEvent):
                            await emit(
                                "tool_call",
                                {
                                    "id": event.part.tool_call_id,
                                    "tool": event.part.tool_name,
                                    "args": event.part.args,
                                },
                            )
                        elif isinstance(event, FunctionToolResultEvent):
                            await emit(
                                "tool_result",
                                {
                                    "id": event.part.tool_call_id,
                                    "tool": event.part.tool_name,
                                    "result": str(event.part.content),
                                },
                            )

    result = run.result
    return {"answer": result.output if result else "", "history": run.result.all_messages()}


def build_graph():
    graph = StateGraph(SearchState)
    graph.add_node("agent", _agent_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile()


# Compiled once; reused across requests.
search_graph = build_graph()
