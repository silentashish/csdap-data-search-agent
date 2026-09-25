"""CSDA STAC API client: item search, heatmap grid URLs, thumbnail rewrite.

Search uses CQL2-JSON; the heatmap is served as Mapbox vector tiles the
panel map consumes directly.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import get_settings
from ..explore.state import ExploreFilters, ResultAsset, ResultItem

_TIMEOUT = 60
_S3_HOST_RE = re.compile(r"^https://[^/]+\.s3\.amazonaws\.com")


# ---------------------------------------------------------------------------
# CQL2 filter builder
# ---------------------------------------------------------------------------
def build_cql2(f: ExploreFilters) -> dict[str, Any]:
    """Translate ExploreFilters into a CQL2-JSON `filter` (op=and)."""
    args: list[dict[str, Any]] = []

    if f.date_start or f.date_end:
        start = f"{f.date_start}T00:00:00Z" if f.date_start else "1900-01-01T00:00:00Z"
        end = f"{f.date_end}T23:59:59Z" if f.date_end else "2100-01-01T00:00:00Z"
        args.append({"op": "anyinteracts", "args": [{"property": "datetime"}, [start, end]]})

    if f.collection_slug:
        args.append({"op": "eq", "args": [{"property": "collection"}, f.collection_slug]})

    geometry = f.geometry
    if geometry is None and f.bbox and len(f.bbox) == 4:
        geometry = _bbox_to_polygon(f.bbox)
    if geometry is not None:
        args.append({"op": "intersects", "args": [{"property": "geometry"}, geometry]})

    if f.cloud_cover_max is not None:
        args.append(
            {"op": "lte", "args": [{"property": "eo:cloud_cover"}, f.cloud_cover_max]}
        )

    if f.item_types and f.item_type_accessor:
        args.append(
            {"op": "in", "args": [{"property": f.item_type_accessor}, list(f.item_types)]}
        )

    # Raw extra predicates (already CQL2-shaped).
    for pred in f.dynamic.values():
        if isinstance(pred, dict):
            args.append(pred)

    return {"op": "and", "args": args} if args else {"op": "and", "args": []}


def _bbox_to_polygon(bbox: list[float]) -> dict[str, Any]:
    w, s, e, n = bbox
    return {
        "type": "Polygon",
        "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search(
    filters: ExploreFilters, token: str | None = None
) -> dict[str, Any]:
    """POST /stac/search. Returns {items, matched, next_token, prev_token}."""
    body: dict[str, Any] = {
        "filter-lang": "cql2-json",
        "filter": build_cql2(filters),
        "sortby": [
            {"field": "datetime", "direction": "desc"},
            {"field": "id", "direction": "asc"},
        ],
        "limit": filters.limit,
    }
    if token:
        body["token"] = token

    url = get_settings().csda_stac_url.rstrip("/") + "/search"
    resp = httpx.post(
        url, json=body, headers={"Prefer": "count=exact"}, timeout=_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()

    items = [parse_feature(feat) for feat in data.get("features", [])]
    next_token = prev_token = None
    for link in data.get("links", []) or []:
        rel = link.get("rel")
        tok = (link.get("body") or {}).get("token")
        if rel == "next":
            next_token = tok
        elif rel == "previous":
            prev_token = tok

    return {
        "items": items,
        "matched": data.get("numberMatched", len(items)),
        "next_token": next_token,
        "prev_token": prev_token,
    }


def collections() -> list[dict[str, Any]]:
    url = get_settings().csda_stac_url.rstrip("/") + "/collections"
    resp = httpx.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("collections", [])


# ---------------------------------------------------------------------------
# Feature parsing
# ---------------------------------------------------------------------------
def rewrite_thumbnail(href: str | None) -> str | None:
    """Rewrite S3 thumbnail hrefs to the public CDN base (matches frontend)."""
    if not href:
        return None
    base = get_settings().thumbnail_base_url.rstrip("/")
    return _S3_HOST_RE.sub(base, href)


def parse_feature(feat: dict[str, Any]) -> ResultItem:
    props = feat.get("properties", {}) or {}
    assets_in = feat.get("assets", {}) or {}

    assets: list[ResultAsset] = []
    total = 0
    thumbnail = None
    for key, a in assets_in.items():
        size = a.get("file:size")
        if key == "thumbnail":
            thumbnail = rewrite_thumbnail(a.get("href"))
            continue
        if isinstance(size, int):
            total += size
        assets.append(
            ResultAsset(
                key=key,
                type=a.get("type"),
                size=size if isinstance(size, int) else None,
                href=a.get("href"),
                title=a.get("title"),
            )
        )

    return ResultItem(
        id=feat.get("id", ""),
        collection=feat.get("collection"),
        datetime=props.get("datetime"),
        cloud_cover=props.get("eo:cloud_cover"),
        thumbnail=thumbnail,
        geometry=feat.get("geometry"),
        bbox=feat.get("bbox"),
        total_size=total or None,
        assets=assets,
    )


# ---------------------------------------------------------------------------
# Heatmap grid (consumed directly by the panel's Mapbox map)
# ---------------------------------------------------------------------------
def heatmap_tile_template(filters: ExploreFilters) -> str | None:
    """MVT tile URL template with {z}/{x}/{y} placeholders for the map source."""
    if not filters.collection_slug:
        return None
    base = get_settings().csda_stac_url.rstrip("/")
    start = filters.date_start or "2000-01-01"
    end = filters.date_end or "2100-01-01"
    qs = "".join(f"?item_types={s}" if i == 0 else f"&item_types={s}"
                 for i, s in enumerate(filters.item_types))
    return (
        f"{base}/heatmap/{{z}}/{{x}}/{{y}}/{start}/{end}/{filters.collection_slug}.mvt{qs}"
    )


def context(zoom: int, filters: ExploreFilters) -> int:
    """GET /context -> max_count, for scaling the heatmap color ramp."""
    if not filters.collection_slug:
        return 0
    base = get_settings().csda_stac_url.rstrip("/")
    start = filters.date_start or "2000-01-01"
    end = filters.date_end or "2100-01-01"
    url = f"{base}/context/{zoom}/{start}/{end}/{filters.collection_slug}.json"
    params = [("item_types", s) for s in filters.item_types]
    resp = httpx.get(
        url, params=params, headers={"Prefer": "count=exact"}, timeout=_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json().get("max_count", 0)
