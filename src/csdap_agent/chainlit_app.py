"""Chainlit UI for the CSDA data-search agent.

- Password auth against Postgres (app_users table).
- Chat persistence + resume via Chainlit's SQLAlchemyDataLayer (Postgres).
- Earthdata credentials collected via chat settings and kept in the user
  session only (never persisted).
- Each message is run through the LangGraph orchestrator.
"""

from __future__ import annotations

from typing import Any

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.input_widget import TextInput
from chainlit.types import ThreadDict

from csdap_agent.agent.graph import search_graph
from csdap_agent.config import get_settings
from csdap_agent.db import conversation, postgres
from csdap_agent.observability import configure_observability

configure_observability()

_EARTHDATA_HINT = (
    "To download data, open **Settings** (⚙️) and enter your "
    "[Earthdata Login](https://urs.earthdata.nasa.gov) credentials "
    "(kept in this session only)."
)


@cl.data_layer
def get_data_layer() -> SQLAlchemyDataLayer:
    """Persist threads/steps to Postgres — powers the history sidebar + resume."""
    return SQLAlchemyDataLayer(conninfo=get_settings().async_pg_dsn)


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> cl.User | None:
    user = postgres.authenticate(username, password)
    if user:
        return cl.User(identifier=user["username"], metadata={"role": user["role"]})
    return None


def _current_thread_id() -> str | None:
    """Chainlit thread id for the active conversation, if persistence is on."""
    try:
        return cl.context.session.thread_id
    except Exception:  # noqa: BLE001
        return cl.user_session.get("thread_id")


async def _send_earthdata_settings() -> None:
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


def _panel_element() -> cl.CustomElement:
    thread_id = _current_thread_id() or "default"
    return cl.CustomElement(
        name="ExplorePanel",
        props={"url": f"/panel/app/index.html?thread={thread_id}"},
    )


async def _show_panel() -> None:
    await cl.ElementSidebar.set_title("🗺 Explore")
    await cl.ElementSidebar.set_elements([_panel_element()])
    cl.user_session.set("panel_open", True)


async def _hide_panel() -> None:
    await cl.ElementSidebar.set_elements([])
    cl.user_session.set("panel_open", False)


@cl.action_callback("toggle_panel")
async def toggle_panel(action: cl.Action) -> None:
    if cl.user_session.get("panel_open"):
        await _hide_panel()
    else:
        await _show_panel()


def _panel_actions() -> list[cl.Action]:
    return [
        cl.Action(
            name="toggle_panel",
            icon="map",
            tooltip="Toggle explore panel",
            payload={},
        )
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    # Earthdata credential inputs. Users fill these to enable downloads.
    await _send_earthdata_settings()

    cl.user_session.set("history", [])
    cl.user_session.set("earthdata", {"username": "", "password": ""})

    await _show_panel()
    await cl.Message(
        content=(
            "**CSDA data-search agent ready.**\n\n"
            "Ask me to find Earth-observation data (dataset, area, dates). "
            "Results appear in the **explore panel** on the right — map, filters "
            "and a downloadable results grid. " + _EARTHDATA_HINT
        ),
        actions=_panel_actions(),
    ).send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict) -> None:
    """Restore a past conversation so the user can keep going.

    Chainlit re-renders the visible transcript automatically; here we reload the
    full agent message history (tool calls included) from Postgres and re-send
    the Earthdata settings widget. Credentials themselves are never persisted,
    so the user re-enters them if they want to download in the resumed session.
    """
    thread_id = thread["id"]
    cl.user_session.set("thread_id", thread_id)
    cl.user_session.set("history", conversation.load_history(thread_id))
    cl.user_session.set("earthdata", {"username": "", "password": ""})

    await _send_earthdata_settings()
    await _show_panel()


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

    # The final reply is a TOP-LEVEL message (no parent step) so it reloads
    # correctly when a chat is resumed. Thinking and tool calls stream into
    # separate collapsible steps as they happen.
    answer_msg = cl.Message(content="")
    ui: dict[str, Any] = {"thinking": None, "tools": {}}

    async def on_event(kind: str, payload: dict[str, Any]) -> None:
        if kind == "thinking":
            step = ui["thinking"]
            if step is None:
                step = cl.Step(name="🧠 Thinking", type="run")
                await step.send()
                ui["thinking"] = step
            await step.stream_token(payload["delta"])
        elif kind == "answer":
            await answer_msg.stream_token(payload["delta"])
        elif kind == "tool_call":
            step = cl.Step(name=f"🔧 {payload['tool']}", type="tool")
            step.input = payload.get("args")
            await step.send()
            ui["tools"][payload["id"]] = step
        elif kind == "tool_result":
            step = ui["tools"].get(payload["id"])
            if step is not None:
                step.output = payload["result"]
                await step.update()

    result = await search_graph.ainvoke(
        {
            "user_input": message.content,
            "earthdata": earthdata,
            "history": history,
            "on_event": on_event,
            "thread_id": _current_thread_id() or "default",
        }
    )

    if ui["thinking"] is not None:
        await ui["thinking"].update()

    # Fallback: if nothing streamed into the answer, use the final output.
    if not answer_msg.content:
        answer_msg.content = result.get("answer") or "(no response)"
    # Force the reply to be a top-level message. Thinking/tool steps are not
    # persisted by the data layer, so a reply parented to one would orphan and
    # vanish on refresh. parent_id=None guarantees it reloads.
    answer_msg.parent_id = None
    await answer_msg.send()

    new_history = result.get("history", history)
    cl.user_session.set("history", new_history)

    # Persist full agent history so this chat can be resumed later.
    thread_id = _current_thread_id()
    if thread_id:
        try:
            conversation.save_history(thread_id, new_history)
        except Exception:  # noqa: BLE001
            pass  # persistence failure must not break the reply
