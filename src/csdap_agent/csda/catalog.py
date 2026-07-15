"""CSDA catalog client: vendors, product types, products, product filters.

These come from the CSDA "signup/vendors" API and describe what data exists and
what filters apply. Public browsing works unauthenticated (visible_in_sdx=true).
The catalog is embedded into Neo4j for semantic lookup (see db.neo4j_store).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings

_TIMEOUT = 60


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    base = get_settings().csda_vendors_url.rstrip("/")
    resp = httpx.get(f"{base}/{path.lstrip('/')}", params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_vendors() -> list[dict[str, Any]]:
    """GET /vendors/?visible_in_sdx=true — data providers."""
    data = _get("vendors/", params={"visible_in_sdx": "true"})
    return sorted(data, key=lambda v: v.get("display_order") or 0)


def fetch_product_types() -> list[dict[str, Any]]:
    """GET /product-types/ — {id, vendors[], name, desc, color}."""
    return _get("product-types/")


def fetch_products() -> list[dict[str, Any]]:
    """GET /products/ — item types per vendor."""
    return _get("products/")


def fetch_product_filters() -> list[dict[str, Any]]:
    """GET /product-filters/ — dynamic filter definitions (cloud cover, etc.)."""
    return _get("product-filters/")


def fetch_all() -> dict[str, list[dict[str, Any]]]:
    """Fetch the entire catalog. Each call is independent and failure-isolated."""
    out: dict[str, list[dict[str, Any]]] = {}
    for key, fn in (
        ("vendors", fetch_vendors),
        ("product_types", fetch_product_types),
        ("products", fetch_products),
        ("product_filters", fetch_product_filters),
    ):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001
            out[key] = []
            out.setdefault("_errors", []).append(f"{key}: {exc}")  # type: ignore[arg-type]
    return out
