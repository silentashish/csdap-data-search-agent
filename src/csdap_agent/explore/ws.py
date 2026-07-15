"""WebSocket sync between the map/filter/results panel and the shared state.

The panel connects to /panel/ws/{thread_id}. It receives the full ExploreState
on connect and after every change (from either the user or the agent), and
sends action messages back when the user interacts with the map/filters/grid.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Query, Response, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..csda import stac
from . import actions
from .state import store

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: dict[str, set[WebSocket]] = {}

    async def connect(self, thread_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._conns.setdefault(thread_id, set()).add(ws)

    def disconnect(self, thread_id: str, ws: WebSocket) -> None:
        conns = self._conns.get(thread_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self._conns.pop(thread_id, None)

    async def broadcast(self, thread_id: str) -> None:
        """Push the current state to every panel connected to this thread."""
        conns = list(self._conns.get(thread_id, ()))
        if not conns:
            return
        payload = {"type": "state", "state": store.get(thread_id).model_dump()}
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(thread_id, ws)


manager = ConnectionManager()


async def _handle_action(thread_id: str, msg: dict[str, Any]) -> None:
    """Apply a panel-originated action to the shared state."""
    state = store.get(thread_id)
    action = msg.get("type")
    payload = msg.get("payload") or {}

    if action == "set_filters":
        actions.merge_filters(state, **payload)
    elif action == "clear_aoi":
        state.filters.bbox = None
        state.filters.geometry = None
        state.bump("AOI cleared")
    elif action == "search":
        await asyncio.to_thread(actions.run_search, state, None)
    elif action == "paginate":
        token = state.next_token if payload.get("direction") == "next" else state.prev_token
        if token:
            await asyncio.to_thread(actions.run_search, state, token)
    elif action == "select":
        actions.set_selection(state, payload.get("item_id", ""), payload.get("asset_keys", []))
    elif action == "get_state":
        pass  # just re-broadcast below


@router.websocket("/panel/ws/{thread_id}")
async def panel_ws(ws: WebSocket, thread_id: str) -> None:
    await manager.connect(thread_id, ws)
    try:
        # Send current state immediately on connect.
        await ws.send_json({"type": "state", "state": store.get(thread_id).model_dump()})
        while True:
            msg = await ws.receive_json()
            await _handle_action(thread_id, msg)
            await manager.broadcast(thread_id)
    except WebSocketDisconnect:
        manager.disconnect(thread_id, ws)
    except Exception:  # noqa: BLE001
        manager.disconnect(thread_id, ws)


@router.get("/panel/config")
async def panel_config() -> dict[str, Any]:
    """Runtime config the panel front-end needs (public values only)."""
    s = get_settings()
    return {
        "mapbox_token": s.mapbox_token,
        "stac_url": s.csda_stac_url,
        "thumbnail_base_url": s.thumbnail_base_url,
        "orders_url": s.csda_orders_url,
    }


# ---------------------------------------------------------------------------
# Same-origin proxies (the browser map/panel can't call csdap directly — CORS).
# ---------------------------------------------------------------------------
@router.get("/panel/collections")
async def panel_collections() -> list[dict[str, Any]]:
    cols = await asyncio.to_thread(stac.collections)
    return [{"id": c.get("id"), "title": c.get("title")} for c in cols]


@router.get("/panel/heatmap/{z}/{x}/{y}.mvt")
async def panel_heatmap(
    z: int, x: int, y: int,
    collection: str = Query(...),
    start: str = Query("2000-01-01"),
    end: str = Query("2100-01-01"),
    item_types: list[str] = Query(default=[]),
) -> Response:
    """Proxy a heatmap vector tile from the CSDA STAC API (same-origin for map)."""
    base = get_settings().csda_stac_url.rstrip("/")
    url = f"{base}/heatmap/{z}/{x}/{y}/{start}/{end}/{collection}.mvt"
    params = [("item_types", s) for s in item_types]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params)
    if r.status_code != 200:
        return Response(status_code=204)  # empty tile
    return Response(
        content=r.content,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/panel/context")
async def panel_context(
    zoom: int = Query(0),
    collection: str = Query(...),
    start: str = Query("2000-01-01"),
    end: str = Query("2100-01-01"),
    item_types: list[str] = Query(default=[]),
) -> dict[str, Any]:
    base = get_settings().csda_stac_url.rstrip("/")
    url = f"{base}/context/{zoom}/{start}/{end}/{collection}.json"
    params = [("item_types", s) for s in item_types]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params, headers={"Prefer": "count=exact"})
    if r.status_code != 200:
        return {"max_count": 0}
    return {"max_count": r.json().get("max_count", 0)}
