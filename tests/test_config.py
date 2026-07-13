"""Smoke tests that don't require external services."""

from csdap_agent.config import Settings
from csdap_agent.db.postgres import _hash_password, _verify_password


def test_pg_dsn_builds_from_parts():
    s = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_host="h",
        postgres_port=1234,
        postgres_db="db",
        database_url=None,
    )
    assert s.pg_dsn == "postgresql://u:p@h:1234/db"


def test_password_hash_roundtrip():
    stored = _hash_password("secret")
    assert _verify_password("secret", stored)
    assert not _verify_password("wrong", stored)
