"""Neo4j vector store.

Two concerns:
- STAC items as vector nodes (:StacItem).
- The CSDA catalog (vendors, product types, products, product filters) as a
  small knowledge graph of :Catalog nodes with embeddings, so the agent can
  resolve natural-language dataset references semantically (requirement #3).
"""

from __future__ import annotations

import json
from typing import Any

from neo4j import Driver, GraphDatabase

from ..config import get_settings

_driver: Driver | None = None

_VECTOR_INDEX = "stac_item_embeddings"
_LABEL = "StacItem"

_CATALOG_INDEX = "catalog_embeddings"
_CATALOG_LABEL = "Catalog"


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
    return _driver


def init_schema() -> None:
    """Create the vector indexes (idempotent). Call once at startup."""
    s = get_settings()
    with get_driver().session() as session:
        for index, label in ((_VECTOR_INDEX, _LABEL), (_CATALOG_INDEX, _CATALOG_LABEL)):
            session.run(
                f"""
                CREATE VECTOR INDEX {index} IF NOT EXISTS
                FOR (n:{label}) ON (n.embedding)
                OPTIONS {{ indexConfig: {{
                    `vector.dimensions`: $dim,
                    `vector.similarity_function`: 'cosine'
                }} }}
                """,
                dim=s.embed_dim,
            )
        session.run(
            f"CREATE CONSTRAINT catalog_uid IF NOT EXISTS "
            f"FOR (n:{_CATALOG_LABEL}) REQUIRE n.uid IS UNIQUE"
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


# ---------------------------------------------------------------------------
# Catalog knowledge graph (vendors / product types / products / filters)
# ---------------------------------------------------------------------------
def _upsert_catalog_node(
    kind: str,
    ext_id: Any,
    name: str,
    text: str,
    embedding: list[float] | None,
    props: dict[str, Any],
) -> None:
    """MERGE a :Catalog node keyed by uid=<kind>:<ext_id>, with an embedding."""
    uid = f"{kind}:{ext_id}"
    params = {
        "uid": uid,
        "kind": kind,
        "ext_id": str(ext_id),
        "name": name,
        "text": text,
        "raw": json.dumps(props, default=str),
        "slug": props.get("slug"),
        "description": props.get("description"),
        "color": props.get("color"),
        "embedding": embedding,
    }
    with get_driver().session() as session:
        session.run(
            f"""
            MERGE (n:{_CATALOG_LABEL} {{uid: $uid}})
            SET n.kind = $kind, n.ext_id = $ext_id, n.name = $name,
                n.text = $text, n.raw = $raw, n.slug = $slug,
                n.description = $description, n.color = $color
            WITH n WHERE $embedding IS NOT NULL
            CALL db.create.setNodeVectorProperty(n, 'embedding', $embedding)
            """,
            **params,
        )


def _relate(from_uid: str, rel: str, to_uid: str) -> None:
    with get_driver().session() as session:
        session.run(
            f"""
            MATCH (a:{_CATALOG_LABEL} {{uid: $from_uid}})
            MATCH (b:{_CATALOG_LABEL} {{uid: $to_uid}})
            MERGE (a)-[:{rel}]->(b)
            """,
            from_uid=from_uid,
            to_uid=to_uid,
        )


def ingest_catalog() -> dict[str, int]:
    """Fetch the CSDA catalog and load it into Neo4j with embeddings + links.

    Returns per-kind counts. Embeddings use the local Ollama embed model.
    """
    # Imported lazily: pulls in langchain-ollama.
    from ..csda import catalog
    from ..llm.models import embed_text

    data = catalog.fetch_all()
    counts = {"vendors": 0, "product_types": 0, "products": 0, "product_filters": 0}

    for v in data.get("vendors", []):
        name = v.get("name") or v.get("slug") or str(v.get("id"))
        text = (
            f"Data vendor {name} ({v.get('full_name') or ''}). "
            f"Provider of satellite imagery. Product types: {v.get('product_types')}. "
            f"{v.get('vendor_citation_information') or ''}"
        ).strip()
        _upsert_catalog_node(
            "vendor", v["id"], name, text, embed_text(text),
            {"slug": v.get("slug"), "item_type_accessor": v.get("item_type_accessor"),
             "has_tasking": v.get("has_tasking"), **v},
        )
        counts["vendors"] += 1

    for pt in data.get("product_types", []):
        name = pt.get("name") or str(pt.get("id"))
        text = f"Product type {name}. {pt.get('desc') or ''}".strip()
        _upsert_catalog_node(
            "product_type", pt["id"], name, text, embed_text(text),
            {"description": pt.get("desc"), "color": pt.get("color"), **pt},
        )
        counts["product_types"] += 1

    for p in data.get("products", []):
        name = p.get("name") or p.get("slug") or str(p.get("id"))
        text = f"Product {name}. {p.get('short_desc') or ''} {p.get('long_desc') or ''}".strip()
        _upsert_catalog_node(
            "product", p["id"], name, text, embed_text(text),
            {"slug": p.get("slug"), "description": p.get("short_desc"),
             "has_search": p.get("has_search"), **p},
        )
        counts["products"] += 1

    for f in data.get("product_filters", []):
        ft = f.get("filter_type") or {}
        name = ft.get("name") or f"filter-{f.get('id')}"
        text = f"Filter {name}. {ft.get('short_desc') or ''}".strip()
        _upsert_catalog_node(
            "product_filter", f["id"], name, text, embed_text(text),
            {"description": ft.get("short_desc"),
             "component_type": ft.get("component_type"),
             "query_statement": f.get("query_statement"), **f},
        )
        counts["product_filters"] += 1

    # Relationships (nodes must exist first).
    for pt in data.get("product_types", []):
        for vid in pt.get("vendors", []) or []:
            _relate(f"product_type:{pt['id']}", "AVAILABLE_FROM", f"vendor:{vid}")
    for p in data.get("products", []):
        if p.get("vendor") is not None:
            _relate(f"product:{p['id']}", "PRODUCED_BY", f"vendor:{p['vendor']}")
        if p.get("type") is not None:
            _relate(f"product:{p['id']}", "OF_TYPE", f"product_type:{p['type']}")
    for f in data.get("product_filters", []):
        if f.get("product") is not None:
            _relate(f"product_filter:{f['id']}", "FILTERS", f"product:{f['product']}")

    return counts


def search_catalog(
    embedding: list[float], limit: int = 5, kind: str | None = None
) -> list[dict[str, Any]]:
    """Semantic search over catalog nodes. Optionally restrict to a kind."""
    with get_driver().session() as session:
        result = session.run(
            f"""
            CALL db.index.vector.queryNodes($index, $limit, $embedding)
            YIELD node, score
            WHERE $kind IS NULL OR node.kind = $kind
            RETURN node.kind AS kind, node.ext_id AS ext_id, node.name AS name,
                   node.slug AS slug, node.description AS description,
                   node.raw AS raw, score
            ORDER BY score DESC
            """,
            index=_CATALOG_INDEX,
            limit=limit * 4,  # over-fetch, then filter by kind
            embedding=embedding,
            kind=kind,
        )
        rows = [dict(r) for r in result]
    return rows[:limit]


def close() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
