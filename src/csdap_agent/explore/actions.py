"""Pure state mutations for the explore panel.

Both the WebSocket layer (user interactions) and the agent tools (LLM
interactions) call these, so filter/search behaviour is identical no matter who
drives it. Broadcasting is the caller's responsibility.
"""

from __future__ import annotations

from typing import Any

from ..csda import stac
from .state import ExploreState


def merge_filters(state: ExploreState, **changes: Any) -> None:
    """Apply non-None filter changes to the state."""
    current = state.filters.model_dump()
    for key, value in changes.items():
        if value is not None and key in current:
            current[key] = value
    state.filters = state.filters.model_validate(current)
    state.bump("filters updated")


def run_search(state: ExploreState, token: str | None = None) -> None:
    """Execute a STAC search with the current filters; store results on state."""
    try:
        result = stac.search(state.filters, token=token)
        state.results = result["items"]
        state.matched = result["matched"]
        state.next_token = result["next_token"]
        state.prev_token = result["prev_token"]
        state.error = None
        state.bump(f"{len(state.results)} of {state.matched} items")
    except Exception as exc:  # noqa: BLE001
        state.error = str(exc)
        state.results = []
        state.bump("search failed")


def set_selection(state: ExploreState, item_id: str, asset_keys: list[str]) -> None:
    if asset_keys:
        state.selected[item_id] = asset_keys
    else:
        state.selected.pop(item_id, None)
    state.bump()
