"""LLM + embedding model factories, both backed by local Ollama.

The chat model is consumed by Pydantic AI via Ollama's OpenAI-compatible
endpoint. Embeddings use langchain-ollama.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_ollama import OllamaEmbeddings
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import get_settings


@lru_cache
def get_chat_model() -> OpenAIModel:
    """Pydantic AI model pointed at Ollama's OpenAI-compatible API."""
    s = get_settings()
    provider = OpenAIProvider(base_url=s.ollama_base_url, api_key=s.ollama_api_key)
    return OpenAIModel(model_name=s.llm_model, provider=provider)


@lru_cache
def get_embeddings() -> OllamaEmbeddings:
    s = get_settings()
    # langchain-ollama talks to the native /api endpoint, not /v1.
    base = s.ollama_base_url.removesuffix("/v1")
    return OllamaEmbeddings(model=s.embed_model, base_url=base)


def embed_text(text: str) -> list[float]:
    return get_embeddings().embed_query(text)
