"""Structured logging (M0-16).

JSON in containers, human-readable in a terminal. Secret-bearing keys are
redacted before anything is emitted (HLD 2.6).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

REDACTED = "***redacted***"
SECRET_HINTS = ("key", "token", "secret", "password", "authorization", "credential")


def _redact(_logger: WrappedLogger, _name: str, event: EventDict) -> EventDict:
    for k in list(event):
        if any(h in k.lower() for h in SECRET_HINTS) and event[k]:
            event[k] = REDACTED
    return event


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO)
    )
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "contour") -> Any:
    return structlog.get_logger(name)
