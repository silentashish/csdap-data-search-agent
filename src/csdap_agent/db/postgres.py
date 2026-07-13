"""Postgres access: connection pool, pgvector document store, and auth helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from psycopg_pool import ConnectionPool

from ..config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(get_settings().pg_dsn, min_size=1, max_size=10, open=True)
    return _pool


def _vec(embedding: list[float]) -> str:
    """Format an embedding as a pgvector literal, e.g. '[0.1,0.2]'."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: bytes | None = None) -> str:
    """PBKDF2 password hash. Format: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>."""
    salt = salt or os.urandom(16)
    iterations = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, iter_s, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iter_s)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def create_user(username: str, password: str, role: str = "user") -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO app_users (username, password_hash, role) VALUES (%s, %s, %s) "
            "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash",
            (username, _hash_password(password), role),
        )


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Return the user record if credentials are valid, else None."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role FROM app_users WHERE username = %s",
            (username,),
        ).fetchone()
    if not row:
        return None
    if not _verify_password(password, row[2]):
        return None
    return {"id": str(row[0]), "username": row[1], "role": row[3]}


# ---------------------------------------------------------------------------
# Vector document store (pgvector)
# ---------------------------------------------------------------------------
def upsert_document(
    source: str,
    content: str,
    embedding: list[float],
    external_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO documents (source, external_id, content, metadata, embedding) "
            "VALUES (%s, %s, %s, %s, %s::vector)",
            (source, external_id, content, json.dumps(metadata or {}), _vec(embedding)),
        )


def similarity_search(
    embedding: list[float], limit: int = 5, source: str | None = None
) -> list[dict[str, Any]]:
    """Cosine similarity search over stored documents."""
    where = "WHERE source = %s" if source else ""
    sql = (
        "SELECT external_id, content, metadata, "
        "1 - (embedding <=> %s::vector) AS score "
        f"FROM documents {where} ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    # embedding appears twice (score + order-by); build params in placeholder order.
    vec = _vec(embedding)
    params: tuple[Any, ...] = (
        (vec, source, vec, limit) if source else (vec, vec, limit)
    )
    with get_pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"external_id": r[0], "content": r[1], "metadata": r[2], "score": float(r[3])}
        for r in rows
    ]
