# CSDAP Agent-Based Data Search

An agent that searches and downloads NASA **CSDA** (Commercial Smallsat Data
Acquisition) Earth-observation data through natural-language chat.

## Stack

| Concern            | Choice                                                        |
| ------------------ | ------------------------------------------------------------- |
| UI                 | Chainlit, mounted on FastAPI                                  |
| Orchestration      | LangGraph (state graph) driving a Pydantic AI agent           |
| LLM                | Local Ollama — `gpt-oss:120b-cloud` (OpenAI-compatible API)   |
| Embeddings         | Ollama `nomic-embed-text` (768-dim) via langchain-ollama      |
| SQL + vector + auth| Postgres + pgvector                                           |
| Vector store       | Neo4j (vector index)                                          |
| CSDA access        | [`csda-client`](https://github.com/NASA-IMPACT/csda-client) + CSDA STAC API |
| Observability      | Logfire (instruments Pydantic AI + HTTPX)                     |

## Architecture

```
Browser ──► FastAPI ──► Chainlit UI ──► LangGraph graph ──► Pydantic AI agent
                                                             │  tools:
                                                             │   • list_collections   (STAC)
                                                             │   • search_stac         (STAC)
                                                             │   • download_asset      (csda-client, Earthdata OAuth)
                                                             │   • semantic_recall     (pgvector)
        Postgres (auth + pgvector)   Neo4j (vector index)   Ollama (LLM + embeddings)
```

## Quick start

1. **Prerequisites**
   - Docker + Docker Compose
   - [Ollama](https://ollama.com) running on the host:
     ```bash
     ollama signin                 # required for free cloud models
     ollama pull nomic-embed-text  # local embedding model
     # gpt-oss:120b-cloud is served from Ollama cloud, no local pull needed
     ```

2. **Configure**
   ```bash
   cp .env.example .env
   # set CHAINLIT_AUTH_SECRET (generate one: `uvx chainlit create-secret`)
   # optionally set LOGFIRE_TOKEN, EARTHDATA_USERNAME/PASSWORD
   ```

3. **Run**
   ```bash
   docker compose up --build
   ```
   - UI:            http://localhost:8000
   - Health:        http://localhost:8000/api/health
   - Neo4j browser: http://localhost:7474

4. **Create a login user**
   ```bash
   docker compose exec app python scripts/create_user.py admin changeme admin
   ```
   Then log in at http://localhost:8000 and, in **Settings**, enter your
   Earthdata Login credentials to enable downloads.

## Local (non-Docker) dev

```bash
uv sync
export PYTHONPATH=src
# point OLLAMA_BASE_URL at http://localhost:11434/v1 and DB hosts at localhost
uvicorn csdap_agent.app:app --reload
```

## Layout

```
src/csdap_agent/
├── app.py            FastAPI app + Chainlit mount
├── chainlit_app.py   Chat UI, auth, Earthdata credential inputs
├── config.py         Settings (pydantic-settings)
├── observability.py  Logfire setup
├── agent/
│   ├── agent.py      Pydantic AI agent + tools
│   └── graph.py      LangGraph orchestration
├── csda/service.py   csda-client + STAC search/download wrapper
├── db/
│   ├── postgres.py   pgvector store + auth
│   └── neo4j_store.py Neo4j vector store
└── llm/models.py     Ollama chat model + embeddings
```

## Explore panel backend (phase 0)

Natural-language queries drive a shared, per-thread `ExploreState` (filters +
results) that both the agent and a map/filter/results panel read and write.

- **Catalog → Neo4j (req #3):** vendors, product types, products and product
  filters are fetched from the CSDA vendors API and embedded into Neo4j as a
  `:Catalog` knowledge graph (`PRODUCED_BY` / `OF_TYPE` / `AVAILABLE_FROM` /
  `FILTERS`). The agent's `find_datasets` tool resolves vague requests to real
  collection slugs via vector search. Ingest:
  ```bash
  docker compose exec app python -c "from csdap_agent.db import neo4j_store; neo4j_store.init_schema(); print(neo4j_store.ingest_catalog())"
  ```
- **STAC search** (`csda/stac.py`): CQL2-JSON `POST /stac/search`, token
  pagination, S3→CDN thumbnail rewrite, heatmap MVT tile-template + context —
  ported from the csdap-frontend query logic.
- **Agent tools**: `find_datasets`, `set_search_filters`, `run_data_search`,
  `paginate_results`, `download_asset`. Each mutates `ExploreState` and
  broadcasts over WebSocket.
- **Sync**: panel connects to `ws://<host>/panel/ws/{thread_id}`; receives the
  full state on connect and after every change; sends `set_filters` / `search` /
  `paginate` / `select` / `clear_aoi` actions back. `GET /panel/config` exposes
  the Mapbox token + STAC/thumbnail/orders URLs.
- **Panel UI** (`static/panel/`): a standalone Mapbox-GL app (map + heatmap grid,
  filters, results grid with thumbnails/size/cloud-cover, select + per-asset
  download links), served at `/panel/app/` and embedded in the Chainlit right
  sidebar via the `ExplorePanel` custom element (`public/elements/`). Toggle it
  with the "🗺 Toggle explore panel" action. Heatmap/collections/context are
  proxied through FastAPI (`/panel/heatmap`, `/panel/collections`,
  `/panel/context`) to keep the browser same-origin.

## Chat history & resume

Past conversations are persisted in Postgres and can be reopened and continued.

- Chainlit's `SQLAlchemyDataLayer` stores threads/steps → a **history sidebar**
  appears and clicking a past chat resumes it. Requires auth (enabled) +
  `CHAINLIT_AUTH_SECRET`.
- The full agent message history (including tool calls / results) is stored
  separately in `conversation_state`, keyed by thread id, so a resumed chat
  continues with complete LLM context — not just the visible text.
- Resume flow lives in `chainlit_app.py` (`@cl.on_chat_resume`) and
  `db/conversation.py`.

> **Schema note:** `docker/postgres/init.sql` runs only on a *fresh* Postgres
> volume. After pulling schema changes, reset the DB volume:
> ```bash
> docker compose down -v && docker compose up --build
> ```

## Notes

- Earthdata credentials entered in the UI live in the Chainlit session only —
  they are never written to disk or the database (re-enter them after resuming
  a chat to download).
- The two vector stores are intentional: Postgres/pgvector backs auth + SQL +
  document recall; Neo4j holds the STAC-item vector graph for relationship-aware
  retrieval as the project grows.
- `csda-client` has no search method, so search runs against the CSDA STAC API;
  authenticated download goes through `csda-client`'s Earthdata OAuth flow.
