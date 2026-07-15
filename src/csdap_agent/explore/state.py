"""Explore panel state: the single source of truth shared by the LLM and the UI.

One `ExploreState` per Chainlit thread. The agent's tools mutate it and the
WebSocket layer broadcasts it to any connected map/filter/results panel; the
panel writes user interactions back into the same object. Both sides therefore
converge on one canonical filter + result set.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExploreFilters(BaseModel):
    """Search filters. Mirrors the csdap-frontend filter state (simplified)."""

    collection_slug: str | None = None          # vendor slug -> STAC `collection eq`
    product_type: str | None = None             # product-type name (informational)
    date_start: str | None = None               # YYYY-MM-DD
    date_end: str | None = None                 # YYYY-MM-DD
    bbox: list[float] | None = None             # [west, south, east, north]
    geometry: dict[str, Any] | None = None      # GeoJSON AOI (overrides bbox if set)
    cloud_cover_max: float | None = None        # % (0-100)
    item_types: list[str] = Field(default_factory=list)   # product slugs
    item_type_accessor: str | None = None       # STAC property for item-type filter
    limit: int = 20
    dynamic: dict[str, Any] = Field(default_factory=dict)  # extra CQL2 predicates


class ResultAsset(BaseModel):
    key: str
    type: str | None = None
    size: int | None = None
    href: str | None = None
    title: str | None = None


class ResultItem(BaseModel):
    """One STAC item, flattened for the results grid."""

    id: str
    collection: str | None = None
    datetime: str | None = None
    cloud_cover: float | None = None
    thumbnail: str | None = None
    geometry: dict[str, Any] | None = None
    bbox: list[float] | None = None
    total_size: int | None = None
    assets: list[ResultAsset] = Field(default_factory=list)


class ExploreState(BaseModel):
    """Canonical per-thread panel state."""

    thread_id: str
    filters: ExploreFilters = Field(default_factory=ExploreFilters)
    results: list[ResultItem] = Field(default_factory=list)
    matched: int = 0
    next_token: str | None = None
    prev_token: str | None = None
    # itemId -> selected asset keys
    selected: dict[str, list[str]] = Field(default_factory=dict)
    status: str = ""
    error: str | None = None
    # Bumped on every mutation so the panel can ignore echoes of its own writes.
    revision: int = 0

    def bump(self, status: str = "") -> None:
        self.revision += 1
        if status:
            self.status = status


class ExploreStore:
    """In-memory registry of per-thread state.

    In-memory is sufficient: the panel is a live view of an active chat, and the
    durable record is the agent history in Postgres. A restart simply requires a
    re-search, which the agent can redo from the persisted conversation.
    """

    def __init__(self) -> None:
        self._states: dict[str, ExploreState] = {}

    def get(self, thread_id: str) -> ExploreState:
        state = self._states.get(thread_id)
        if state is None:
            state = ExploreState(thread_id=thread_id)
            self._states[thread_id] = state
        return state

    def set(self, state: ExploreState) -> None:
        self._states[state.thread_id] = state


# Process-wide singleton.
store = ExploreStore()
