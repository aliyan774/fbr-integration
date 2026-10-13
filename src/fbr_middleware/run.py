"""Start uvicorn single-process on LISTEN_HOST:LISTEN_PORT (no --workers: Windows friendly)."""

from __future__ import annotations

import uvicorn

from .config import get_settings
from .logging_setup import setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(settings.LOG_DIR, settings.LOG_LEVEL)
    uvicorn.run(
        "fbr_middleware.main:app",
        host=settings.LISTEN_HOST,
        port=settings.LISTEN_PORT,
        log_config=None,  # keep our rotating-file + redaction logging
        ssl_certfile=settings.SSL_CERTFILE or None,
        ssl_keyfile=settings.SSL_KEYFILE or None,
        # Deliberately no `workers=`: multi-process uvicorn relies on fork semantics that are
        # unreliable on Windows. One async process handles tens of concurrent SAP calls.
    )


if __name__ == "__main__":
    main()
