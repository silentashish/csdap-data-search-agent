"""FastAPI application with the Chainlit UI mounted at the root path.

REST endpoints live under /api; the chat UI is served at /.
"""

from __future__ import annotations

import os

from chainlit.utils import mount_chainlit
from fastapi import FastAPI

from .config import get_settings
from .db import neo4j_store
from .observability import configure_observability

configure_observability()

app = FastAPI(title="CSDAP Agent", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    # Neo4j vector index is safe to (re)create on every boot.
    try:
        neo4j_store.init_schema()
    except Exception:  # noqa: BLE001
        # Don't block API startup if Neo4j isn't up yet.
        pass


@app.get("/api/health")
async def health() -> dict[str, str]:
    s = get_settings()
    return {"status": "ok", "model": s.llm_model}


# Mount Chainlit UI at "/". Path is resolved relative to this file.
_chainlit_target = os.path.join(os.path.dirname(__file__), "chainlit_app.py")
mount_chainlit(app=app, target=_chainlit_target, path="/")
