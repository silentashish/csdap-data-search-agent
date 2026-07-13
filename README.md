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

## Notes

- Earthdata credentials entered in the UI live in the Chainlit session only —
  they are never written to disk or the database.
- The two vector stores are intentional: Postgres/pgvector backs auth + SQL +
  document recall; Neo4j holds the STAC-item vector graph for relationship-aware
  retrieval as the project grows.
- `csda-client` has no search method, so search runs against the CSDA STAC API;
  authenticated download goes through `csda-client`'s Earthdata OAuth flow.
