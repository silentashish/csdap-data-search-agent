"""Pydantic AI agent that searches CSDA data and can download assets.

The LLM decides which tools to call; tools wrap the CSDA STAC API and
csda-client. This agent node is driven by the LangGraph orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext

from ..csda.service import CsdaService
from ..llm.models import embed_text, get_chat_model
from ..db import postgres


@dataclass
class AgentDeps:
    """Runtime dependencies injected into every tool call."""

    csda: CsdaService


SYSTEM_PROMPT = """\
You are a data-search assistant for NASA's CSDA (Commercial Smallsat Data
Acquisition) program. Help users find and download Earth-observation data.

Workflow:
1. Use `list_collections` to discover available datasets when the user is vague.
2. Use `search_stac` to find items matching their area, time range, and dataset.
3. Summarize matches clearly (collection, item id, datetime, cloud cover, assets).
4. Only call `download_asset` when the user explicitly asks to download and has
   provided Earthdata credentials.

Be concise. When you lack a bounding box or date range, ask for it rather than
guessing. Never invent item ids or collection names.
"""

data_search_agent: Agent[AgentDeps, str] = Agent(
    get_chat_model(),
    deps_type=AgentDeps,
    system_prompt=SYSTEM_PROMPT,
)


@data_search_agent.tool
async def list_collections(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """List available CSDA STAC collections (id + title + description)."""
    cols = ctx.deps.csda.list_collections()
    return [
        {"id": c.get("id"), "title": c.get("title"), "description": c.get("description")}
        for c in cols
    ]


@data_search_agent.tool
async def search_stac(
    ctx: RunContext[AgentDeps],
    collections: list[str] | None = None,
    bbox: list[float] | None = None,
    datetime_range: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search CSDA STAC items.

    Args:
        collections: STAC collection ids to restrict the search to.
        bbox: [west, south, east, north] in decimal degrees.
        datetime_range: RFC3339 range, e.g. "2023-01-01T00:00:00Z/2023-02-01T00:00:00Z".
        limit: Max items to return.
    """
    items = ctx.deps.csda.search_items(
        collections=collections, bbox=bbox, datetime_range=datetime_range, limit=limit
    )
    results = []
    for it in items:
        props = it.get("properties", {})
        results.append(
            {
                "id": it.get("id"),
                "collection": it.get("collection"),
                "datetime": props.get("datetime"),
                "cloud_cover": props.get("eo:cloud_cover"),
                "assets": list((it.get("assets") or {}).keys()),
            }
        )
    return results


@data_search_agent.tool
async def download_asset(
    ctx: RunContext[AgentDeps], collection_id: str, item_id: str, asset_key: str
) -> str:
    """Download a single asset. Requires the user's Earthdata credentials to be set."""
    if not ctx.deps.csda.credentials:
        return "No Earthdata credentials provided. Ask the user to enter them first."
    path = ctx.deps.csda.download(collection_id, item_id, asset_key)
    return f"Downloaded to {path}"


@data_search_agent.tool_plain
async def semantic_recall(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Recall previously seen items/notes semantically similar to `query`."""
    try:
        emb = embed_text(query)
        return postgres.similarity_search(emb, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"recall unavailable: {exc}"}]
