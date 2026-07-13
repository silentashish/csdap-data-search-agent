"""Chainlit UI for the CSDA data-search agent.

- Password auth against Postgres (app_users table).
- Earthdata credentials collected via chat settings and kept in the user
  session only (never persisted).
- Each message is run through the LangGraph orchestrator.
"""

from __future__ import annotations

import chainlit as cl
from chainlit.input_widget import TextInput

from .agent.graph import search_graph
from .db import postgres
from .observability import configure_observability

configure_observability()


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> cl.User | None:
    user = postgres.authenticate(username, password)
    if user:
        return cl.User(identifier=user["username"], metadata={"role": user["role"]})
    return None


@cl.on_chat_start
async def on_chat_start() -> None:
    # Earthdata credential inputs. Users fill these to enable downloads.
    await cl.ChatSettings(
        [
            TextInput(
                id="earthdata_username",
                label="Earthdata Username",
                placeholder="urs.earthdata.nasa.gov username",
            ),
            TextInput(
                id="earthdata_password",
                label="Earthdata Password",
                placeholder="•••••••• (kept in session only)",
            ),
        ]
    ).send()

    cl.user_session.set("history", [])
    cl.user_session.set("earthdata", {"username": "", "password": ""})

    await cl.Message(
        content=(
            "**CSDA data-search agent ready.**\n\n"
            "Ask me to find Earth-observation data (dataset, area, dates). "
            "To download, open **Settings** (gear icon) and enter your "
            "Earthdata Login credentials first."
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict) -> None:
    cl.user_session.set(
        "earthdata",
        {
            "username": settings.get("earthdata_username", ""),
            "password": settings.get("earthdata_password", ""),
        },
    )
    msg = "Earthdata credentials saved for this session." if settings.get(
        "earthdata_username"
    ) else "Earthdata credentials cleared."
    await cl.Message(content=msg).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    history = cl.user_session.get("history") or []
    earthdata = cl.user_session.get("earthdata")

    async with cl.Step(name="agent"):
        result = await search_graph.ainvoke(
            {
                "user_input": message.content,
                "earthdata": earthdata,
                "history": history,
            }
        )

    cl.user_session.set("history", result.get("history", history))
    await cl.Message(content=result.get("answer", "(no response)")).send()
