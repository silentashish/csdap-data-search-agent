"""Persist Pydantic AI message history per Chainlit thread.

Chainlit's data layer already stores the *visible* transcript (threads/steps),
which powers the history sidebar and resume UI. But resuming a chat also needs
the agent's *full* history — including tool calls and tool results — so the LLM
continues with complete context. That richer history lives here, keyed by the
Chainlit thread id.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from .postgres import get_pool


def save_history(thread_id: str, messages: list[ModelMessage]) -> None:
    """Upsert the serialized agent history for a thread."""
    payload = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO conversation_state (thread_id, history, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (thread_id) DO UPDATE "
            "SET history = EXCLUDED.history, updated_at = now()",
            (thread_id, payload),
        )


def load_history(thread_id: str) -> list[ModelMessage]:
    """Load and validate the agent history for a thread (empty if none)."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT history FROM conversation_state WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
    if not row or not row[0]:
        return []
    # psycopg returns jsonb as a parsed Python object.
    return ModelMessagesTypeAdapter.validate_python(row[0])
