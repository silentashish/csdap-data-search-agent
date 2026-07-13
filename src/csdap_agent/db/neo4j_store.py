"""Neo4j vector store. Stores STAC items as nodes with an embedding vector index."""

from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase

from ..config import get_settings

_driver: Driver | None = None

_VECTOR_INDEX = "stac_item_embeddings"
_LABEL = "StacItem"


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
    return _driver


def init_schema() -> None:
    """Create the vector index (idempotent). Call once at startup."""
    s = get_settings()
    with get_driver().session() as session:
        session.run(
            f"""
            CREATE VECTOR INDEX {_VECTOR_INDEX} IF NOT EXISTS
            FOR (n:{_LABEL}) ON (n.embedding)
            OPTIONS {{ indexConfig: {{
                `vector.dimensions`: $dim,
                `vector.similarity_function`: 'cosine'
            }} }}
            """,
            dim=s.embed_dim,
        )


def upsert_item(item_id: str, content: str, embedding: list[float], metadata: dict[str, Any]) -> None:
    with get_driver().session() as session:
        session.run(
            f"""
            MERGE (n:{_LABEL} {{id: $id}})
            SET n.content = $content, n.metadata = $metadata
            WITH n
            CALL db.create.setNodeVectorProperty(n, 'embedding', $embedding)
            """,
            id=item_id,
            content=content,
            embedding=embedding,
            metadata=metadata,
        )


def similarity_search(embedding: list[float], limit: int = 5) -> list[dict[str, Any]]:
    with get_driver().session() as session:
        result = session.run(
            f"""
            CALL db.index.vector.queryNodes($index, $limit, $embedding)
            YIELD node, score
            RETURN node.id AS id, node.content AS content,
                   node.metadata AS metadata, score
            """,
            index=_VECTOR_INDEX,
            limit=limit,
            embedding=embedding,
        )
        return [dict(record) for record in result]


def close() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
