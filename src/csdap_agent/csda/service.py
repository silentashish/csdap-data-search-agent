"""Thin service layer over csda-client + the CSDA STAC API.

Search is done against the STAC API (the csda-client itself has no search
method); authenticated download uses csda-client, which handles the Earthdata
Login OAuth flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from csda_client import CsdaClient
from httpx import BasicAuth

from ..config import get_settings


@dataclass
class EarthdataCredentials:
    username: str
    password: str

    def as_auth(self) -> BasicAuth:
        return BasicAuth(username=self.username, password=self.password)


class CsdaService:
    """Per-user CSDA access. Instantiate with the user's Earthdata credentials."""

    def __init__(self, credentials: EarthdataCredentials | None = None) -> None:
        self.settings = get_settings()
        self.credentials = credentials
        self._client: CsdaClient | None = None

    # ---- Auth ----
    def login(self) -> str:
        """Log in with the provided credentials and return the verify() response."""
        if not self.credentials:
            raise ValueError("Earthdata credentials required to log in")
        self._client = CsdaClient.open(self.credentials.as_auth(), url=self.settings.csda_url)
        return self._client.verify()

    @property
    def client(self) -> CsdaClient:
        if self._client is None:
            self.login()
        assert self._client is not None
        return self._client

    # ---- Search (STAC API, no auth required for browsing) ----
    def search_items(
        self,
        collections: list[str] | None = None,
        bbox: list[float] | None = None,
        datetime_range: str | None = None,
        query: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """POST /search against the CSDA STAC API. Returns raw STAC item dicts."""
        body: dict[str, Any] = {"limit": limit}
        if collections:
            body["collections"] = collections
        if bbox:
            body["bbox"] = bbox
        if datetime_range:
            body["datetime"] = datetime_range
        if query:
            body["query"] = query

        url = self.settings.csda_stac_url.rstrip("/") + "/search"
        resp = httpx.post(url, json=body, timeout=60)
        resp.raise_for_status()
        return resp.json().get("features", [])

    def list_collections(self) -> list[dict[str, Any]]:
        url = self.settings.csda_stac_url.rstrip("/") + "/collections"
        resp = httpx.get(url, timeout=60)
        resp.raise_for_status()
        return resp.json().get("collections", [])

    # ---- Vendors / products (authenticated) ----
    def vendors(self) -> list[dict[str, Any]]:
        return [v.model_dump() for v in self.client.vendors()]

    # ---- Download (authenticated) ----
    def download(self, collection_id: str, item_id: str, asset_key: str) -> str:
        """Download an asset to DOWNLOAD_DIR and return the local path."""
        out_dir = Path(self.settings.download_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{collection_id}__{item_id}__{asset_key}"
        self.client.download(collection_id, item_id, asset_key, dest)
        return str(dest)
