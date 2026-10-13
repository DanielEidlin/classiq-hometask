"""structlog configuration shared by every service.

Logs are JSON lines on stdout by default. Set `LOG_FORMAT=console` for human-readable output.
"""

import functools
import logging
import os
import sys

import structlog


def _add_service(
    service: str, logger: object, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    event_dict["service"] = service
    return event_dict


def configure_logging(service: str) -> None:
    renderer: structlog.typing.Processor
    if os.environ.get("LOG_FORMAT", "json").lower() == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            functools.partial(_add_service, service),
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
