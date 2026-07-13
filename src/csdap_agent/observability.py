"""Logfire setup. Instruments Pydantic AI, HTTPX, and FastAPI when configured."""

from __future__ import annotations

import logging

import logfire

from .config import get_settings

_configured = False


def configure_observability() -> None:
    """Idempotent Logfire configuration.

    Runs in local/console mode when no token is set, so nothing leaves the
    machine unless LOGFIRE_TOKEN + LOGFIRE_SEND_TO_LOGFIRE are provided.
    """
    global _configured
    if _configured:
        return

    settings = get_settings()
    logfire.configure(
        service_name=settings.logfire_service_name,
        send_to_logfire="if-token-present" if settings.logfire_send_to_logfire else False,
        token=settings.logfire_token or None,
        console=logfire.ConsoleOptions(min_log_level="info"),
    )

    # Instrument common libraries; guarded so missing optional deps don't crash.
    try:
        logfire.instrument_pydantic_ai()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug("pydantic-ai instrumentation unavailable")
    try:
        logfire.instrument_httpx()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug("httpx instrumentation unavailable")

    _configured = True
