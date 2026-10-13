"""Logging: rotating files under LOG_DIR plus console, with Authorization redaction."""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

# "Authorization: Bearer abc", "authorization='Bearer abc'", "Authorization=abc"
_AUTH_RE = re.compile(r"(?i)(authorization\s*[:=]\s*[\"']?)(bearer\s+)?[^\s\"',}]+")
# bare "Bearer <token>" anywhere in a message
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")

REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    """Replace any Authorization header value or bare 'Bearer xxx' token with a placeholder."""
    text = _AUTH_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    return text


class RedactAuthorizationFilter(logging.Filter):
    """Logging filter that scrubs bearer tokens from the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = redact(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


_configured_dir: Path | None = None
_handlers: list[logging.Handler] = []


def current_log_file() -> Path | None:
    """Path of the live log file once setup_logging has run (used by tests and ops)."""
    return _configured_dir / "app.log" if _configured_dir else None


def _dated_name(default_name: str) -> str:
    """Turn 'app.log.2026-10-13' (handler default) into 'app-2026-10-13.log'."""
    base, _, suffix = default_name.rpartition(".log.")
    if not suffix:
        return default_name
    return f"{base}-{suffix}.log"


def setup_logging(log_dir: str | Path, level: str = "INFO") -> None:
    """Idempotent per log directory; a different directory replaces the handlers."""
    global _configured_dir
    log_path = Path(log_dir).resolve()
    root = logging.getLogger()
    if _configured_dir == log_path:
        return
    for h in _handlers:
        root.removeHandler(h)
        h.close()
    _handlers.clear()
    _configured_dir = log_path
    log_path.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    redactor = RedactAuthorizationFilter()

    # app.log is the live file; at midnight it is renamed app-YYYY-MM-DD.log, 30 days kept.
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_path / "app.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    file_handler.namer = lambda name: _dated_name(name)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redactor)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redactor)

    root.setLevel(level.upper())
    root.addHandler(file_handler)
    root.addHandler(console)
    _handlers.extend([file_handler, console])

    # uvicorn and httpx loggers also go through the redaction filter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.addFilter(redactor)
        for h in lg.handlers:
            h.addFilter(redactor)
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("httpcore").setLevel("WARNING")
    logging.getLogger("aiosqlite").setLevel("WARNING")
