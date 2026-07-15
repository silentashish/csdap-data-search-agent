"""Central configuration, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- LLM (Ollama, OpenAI-compatible) ----
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_api_key: str = "ollama"
    llm_model: str = "gpt-oss:120b-cloud"
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # ---- Postgres ----
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "csdap"
    postgres_user: str = "csdap"
    postgres_password: str = "csdap_dev_password"
    database_url: str | None = None

    # ---- Neo4j ----
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j_dev_password"

    # ---- CSDA / Earthdata ----
    # Base host used by csda-client (Earthdata OAuth + download).
    csda_url: str = "https://csdap.earthdata.nasa.gov"
    # STAC API root (search + heatmap + context). Public for browsing.
    csda_stac_url: str = "https://csdap.earthdata.nasa.gov/stac"
    # Vendors / products / product-types / product-filters catalog API.
    csda_vendors_url: str = "https://csdap.earthdata.nasa.gov/signup/vendors/api"
    # Orders API (download links + order creation).
    csda_orders_url: str = "https://csdap.earthdata.nasa.gov/api"
    # Thumbnails: STAC returns S3 hrefs that are rewritten to this CDN base.
    thumbnail_base_url: str = "https://csdap.earthdata.nasa.gov/thumbnails"
    earthdata_username: str = ""
    earthdata_password: str = ""
    download_dir: str = "/data/downloads"

    # ---- Map panel (front-end) ----
    # Public Mapbox token (same one the live csdap explore page uses). Swappable.
    mapbox_token: str = (
        "pk.eyJ1IjoiZGV2c2VlZCIsImEiOiJjazB6YXU2bDUwMWNkM2VvNGNpMnFhOXMxIn0"
        ".c30a2TQIfCDF3GlqMdSQ_g"
    )

    # ---- Logfire ----
    logfire_token: str = ""
    logfire_service_name: str = "csdap-agent"
    logfire_send_to_logfire: bool = False

    # ---- Chainlit ----
    chainlit_auth_secret: str = Field(default="change-me")

    @property
    def pg_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_pg_dsn(self) -> str:
        """asyncpg-driver DSN required by Chainlit's SQLAlchemyDataLayer."""
        return self.pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
