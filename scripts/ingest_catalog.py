"""Ingest the CSDA catalog (vendors/products/types/filters) into Neo4j.

Usage (inside the app container):
    docker compose exec app python scripts/ingest_catalog.py
"""

from __future__ import annotations

from csdap_agent.db import neo4j_store


def main() -> None:
    neo4j_store.init_schema()
    print("Ingesting CSDA catalog into Neo4j (this embeds each node)...")
    counts = neo4j_store.ingest_catalog()
    print("Done:", counts)


if __name__ == "__main__":
    main()
