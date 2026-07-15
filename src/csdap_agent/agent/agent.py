"""Pydantic AI agent that searches CSDA data and drives the explore panel.

Tools mutate the per-thread ExploreState (shared with the map/filter/results
panel over WebSocket) and broadcast the change, so a natural-language query
updates the same UI the user can also drive by hand.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext

from ..csda import stac
from ..csda.service import CsdaService
from ..db import neo4j_store
from ..explore import actions
from ..explore.state import store
from ..explore.ws import manager
from ..llm.models import embed_text, get_chat_model


@dataclass
class AgentDeps:
    """Runtime dependencies injected into every tool call."""

    csda: CsdaService
    thread_id: str = "default"


SYSTEM_PROMPT = """\
You are a data-search assistant for NASA's CSDA (Commercial Smallsat Data
Acquisition) program. You control an explore panel (map + filters + results
grid) shown beside this chat. Your tool calls update that panel live.

Workflow:
1. `find_datasets` — resolve the user's request to a real dataset/collection
   (semantic search over the CSDA catalog). Use it whenever the dataset is
   implied by description ("high-res optical", "radar", a vendor name).
2. `set_search_filters` — set collection_slug, date range, bounding box, and
   cloud-cover ceiling from the user's request.
3. `run_data_search` — execute the search; results populate the panel grid.
4. `paginate_results` — page through matches when asked.
5. `download_asset` — only when the user explicitly asks to download AND has
   entered Earthdata credentials.

Rules:
- Prefer real collection slugs returned by `find_datasets`; never invent them.
- If you lack an area or date range, set what you have and tell the user the
  results are broad, or ask for a bbox/date range.
- Keep replies concise; the panel shows the detailed results, so summarize.
"""

data_search_agent: Agent[AgentDeps, str] = Agent(
    get_chat_model(),
    deps_type=AgentDeps,
    system_prompt=SYSTEM_PROMPT,
)


async def _sync_panel(deps: AgentDeps) -> None:
    await manager.broadcast(deps.thread_id)


@data_search_agent.tool
async def find_datasets(ctx: RunContext[AgentDeps], query: str) -> list[dict[str, Any]]:
    """Semantically search the CSDA catalog (vendors, product types, products).

    Returns candidates with their `slug` — use a vendor/collection slug for
    `set_search_filters(collection_slug=...)`.
    """
    try:
        emb = embed_text(query)
        hits = neo4j_store.search_catalog(emb, limit=8)
        return [
            {"kind": h["kind"], "name": h["name"], "slug": h.get("slug"),
             "description": h.get("description"), "score": round(h["score"], 3)}
            for h in hits
        ]
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"catalog search unavailable: {exc}"}]


@data_search_agent.tool
async def set_search_filters(
    ctx: RunContext[AgentDeps],
    collection_slug: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    bbox: list[float] | None = None,
    cloud_cover_max: float | None = None,
    item_types: list[str] | None = None,
) -> dict[str, Any]:
    """Update the explore-panel filters (only provided fields change).

    Args:
        collection_slug: STAC collection / vendor slug (from `find_datasets`).
        date_start: YYYY-MM-DD.
        date_end: YYYY-MM-DD.
        bbox: [west, south, east, north] decimal degrees.
        cloud_cover_max: max cloud cover percent (0-100).
        item_types: product-type slugs to restrict to.
    """
    state = store.get(ctx.deps.thread_id)
    actions.merge_filters(
        state,
        collection_slug=collection_slug,
        date_start=date_start,
        date_end=date_end,
        bbox=bbox,
        cloud_cover_max=cloud_cover_max,
        item_types=item_types,
    )
    await _sync_panel(ctx.deps)
    return state.filters.model_dump(exclude_none=True)


@data_search_agent.tool
async def run_data_search(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
    """Run the STAC search with the current filters; results appear in the panel."""
    state = store.get(ctx.deps.thread_id)
    if not state.filters.collection_slug:
        return {"error": "No collection set. Call set_search_filters(collection_slug=...) first."}
    await asyncio.to_thread(actions.run_search, state, None)
    await _sync_panel(ctx.deps)
    if state.error:
        return {"error": state.error}
    return {
        "matched": state.matched,
        "returned": len(state.results),
        "has_more": bool(state.next_token),
        "items": [
            {"id": r.id, "datetime": r.datetime, "cloud_cover": r.cloud_cover,
             "assets": len(r.assets)}
            for r in state.results[:10]
        ],
    }


@data_search_agent.tool
async def paginate_results(ctx: RunContext[AgentDeps], direction: str = "next") -> dict[str, Any]:
    """Load the next or previous page of results ('next' | 'previous')."""
    state = store.get(ctx.deps.thread_id)
    token = state.next_token if direction == "next" else state.prev_token
    if not token:
        return {"error": f"no {direction} page available"}
    await asyncio.to_thread(actions.run_search, state, token)
    await _sync_panel(ctx.deps)
    return {"matched": state.matched, "returned": len(state.results),
            "has_more": bool(state.next_token)}


@data_search_agent.tool
async def download_asset(
    ctx: RunContext[AgentDeps], collection_id: str, item_id: str, asset_key: str
) -> str:
    """Download a single asset. Requires the user's Earthdata credentials to be set."""
    if not ctx.deps.csda.credentials:
        return "No Earthdata credentials provided. Ask the user to enter them first."
    path = ctx.deps.csda.download(collection_id, item_id, asset_key)
    return f"Downloaded to {path}"
