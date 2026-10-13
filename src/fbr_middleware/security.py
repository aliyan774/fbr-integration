"""Authentication dependencies for the SAP-facing endpoints.

- X-API-Key authenticates SAP to the middleware (MIDDLEWARE_API_KEYS).
- Authorization: Bearer <PRAL token> is checked for shape only and handed to the
  PRAL client untouched. It is never stored or logged.
"""

from __future__ import annotations

import hmac
import re

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings

_BEARER_RE = re.compile(r"^\s*Bearer\s+(?P<token>\S+)\s*$", re.IGNORECASE)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> str:
    if not x_api_key:
        raise _unauthorized("Missing X-API-Key header")
    for key in settings.api_keys:
        if hmac.compare_digest(key, x_api_key):
            return x_api_key
    raise _unauthorized("Invalid X-API-Key")


async def require_bearer(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Return the raw PRAL token from a well-formed ``Authorization: Bearer <token>`` header."""
    if not authorization:
        raise _unauthorized("Missing Authorization: Bearer header")
    m = _BEARER_RE.match(authorization)
    if not m:
        raise _unauthorized("Malformed Authorization header; expected 'Bearer <token>'")
    return m.group("token")
