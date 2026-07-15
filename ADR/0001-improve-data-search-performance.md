# Architecture Decision Record (ADR) - Data Search Performance Improvements

## Context
The CSDA Agent handles natural language queries by parsing them into semantic searches over the catalog (via Neo4j) and structural/geospatial searches for actual data (via STAC API). As the volume of data queried increases, we have identified several performance bottlenecks in the current search implementation that cause elevated latency and slow responses for the user.

This document outlines three key architectural improvements to address these bottlenecks and significantly improve the performance of data search.

---

## ADR 1: Remove Exact Counting from STAC API Requests

### Context
In the STAC API client (`src/csdap_agent/csda/stac.py`), all POST search requests currently include the header `Prefer: count=exact`. While this provides an accurate total match count (`numberMatched`), it forces the underlying database (e.g., PostGIS) to perform a full scan of all records matching the query parameters instead of returning just the requested page of items. For large STAC catalogs, this single header can degrade query performance by orders of magnitude (often turning sub-second queries into queries taking 10+ seconds).

### Decision
We will remove the `Prefer: count=exact` header from standard data search requests in the STAC API client. If the backend supports estimated counts without the exact count header, we will rely on those. If exact counts are absolutely required for specific UI functionalities, we will isolate that requirement to a separate, dedicated endpoint or flag, rather than applying it broadly to all searches.

### Consequences
- **Positive:** STAC search latency will decrease dramatically, improving agent response time.
- **Negative:** The explore panel might no longer show an exact "total items matched" number for large result sets. The UI will need to be adapted to display "results available" or an estimated match count instead.

---

## ADR 2: Migrate to Asynchronous STAC API Client

### Context
The current STAC client uses synchronous HTTP requests (`httpx.post()`, `httpx.get()`). To prevent blocking the async event loop of the Chainlit agent, the tool `run_data_search` wraps the synchronous `actions.run_search` function using `asyncio.to_thread`. While this prevents the main loop from stalling, it still introduces thread pool overhead and consumes a thread per active search, limiting scalability under concurrent load.

### Decision
We will refactor `src/csdap_agent/csda/stac.py` to use `httpx.AsyncClient` for all STAC API interactions, and propagate `async`/`await` up through `actions.run_search` to the agent tool. 

### Consequences
- **Positive:** Improved concurrency and reduced thread pool overhead. The agent will scale much better when handling multiple concurrent users or multi-agent workflows.
- **Positive:** Cleaner async codebase consistency.
- **Negative:** Requires refactoring the `actions.py` state mutators to be async, which could have cascading effects on other synchronous code paths interacting with the state.

---

## ADR 3: Implement Vector Index Pre-Filtering in Neo4j

### Context
When the agent executes `find_datasets`, it calls `neo4j_store.search_catalog` to perform a semantic search. The current implementation fetches candidate records from the vector index with an inflated limit (`limit * 4`) and then applies a `WHERE` clause (`WHERE $kind IS NULL OR node.kind = $kind`) to post-filter the results down to the desired kind. Post-filtering vector results can lead to inaccuracies and empty result sets if all top neighbors fall outside the target `kind`, and fetching extra nodes wastes memory and database bandwidth.

### Decision
We will update the Neo4j queries in `src/csdap_agent/db/neo4j_store.py` to leverage native vector index pre-filtering (if supported by our Neo4j version) or utilize filtered vector search techniques so the index lookup natively bounds the nearest neighbors to the required `kind`. 

### Consequences
- **Positive:** More accurate semantic search results, guaranteeing that we retrieve up to `limit` valid results for the target category.
- **Positive:** Marginal performance improvement and reduced data transfer between the database and application.
- **Negative:** Dependent on the version of Neo4j being used (requires Neo4j 5.18+ for native pre-filtering support on vector indexes). We must ensure compatibility.
