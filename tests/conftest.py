"""Shared fixtures: isolated settings, app with lifespan, ASGI client, respx PRAL mock, sample body."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import respx

from fbr_middleware.config import FbrEnvironment, Settings, get_settings, reset_settings_cache

REPO = Path(__file__).resolve().parents[1]
POSTMAN = REPO / "postman" / "FBR-Digital-Invoicing.postman_collection.json"

API_KEY = "test-api-key"
DEV_URL = "/api/v1/dev/invoices"
PRD_URL = "/api/v1/prd/invoices"
TOKEN = "secret-pral-token-ABC123xyz"
PRAL_BASE = "https://pral.test"


def _postman_request(folder: str, name_prefix: str) -> dict[str, Any]:
    col = json.loads(POSTMAN.read_text(encoding="utf-8"))
    for grp in col["item"]:
        if grp["name"] == folder:
            for it in grp["item"]:
                if it["name"].startswith(name_prefix):
                    return it
    raise KeyError(f"{folder}/{name_prefix}")


def _postman_example(folder: str, name_prefix: str, example_name: str) -> dict[str, Any]:
    it = _postman_request(folder, name_prefix)
    for ex in it["response"]:
        if ex["name"] == example_name:
            return json.loads(ex["body"])
    raise KeyError(example_name)


#: PRAL sample bodies from the Postman collection (spec v1.12 samples)
SAMPLE_INVOICE: dict[str, Any] = json.loads(_postman_request("Sandbox", "2.")["request"]["body"]["raw"])
VALIDATE_OK: dict[str, Any] = _postman_example("Sandbox", "1.", "Valid")
VALIDATE_ITEM_INVALID: dict[str, Any] = _postman_example("Sandbox", "1.", "Invalid")
POST_OK: dict[str, Any] = _postman_example("Sandbox", "2.", "Valid")
POST_HEADER_INVALID: dict[str, Any] = _postman_example("Sandbox", "2.", "Invalid (header-level)")
POST_ITEM_INVALID: dict[str, Any] = _postman_example("Sandbox", "2.", "Invalid (item-level)")


def sample_submission(erp_no: str = "INV-0001", **overrides: Any) -> dict[str, Any]:
    body = {
        "erpInvoiceNo": erp_no,
        "sellerNtn": SAMPLE_INVOICE["sellerNTNCNIC"],
        "callbackRef": "1000/2025",
        "invoice": copy.deepcopy(SAMPLE_INVOICE),
    }
    body.update(overrides)
    return body


def auth_headers(token: str = TOKEN, api_key: str = API_KEY) -> dict[str, str]:
    return {"X-API-Key": api_key, "Authorization": f"Bearer {token}"}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Sandbox settings against a fake PRAL host and a throw-away SQLite file."""
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "no-such.env"))  # never read a developer's .env
    monkeypatch.setenv("FBR_BASE_URL", PRAL_BASE)
    monkeypatch.setenv("FBR_DEV_BASE_URL", "")
    monkeypatch.setenv("FBR_PRD_BASE_URL", "")
    monkeypatch.setenv("FBR_ENABLED_ENVS", "dev,prd")
    monkeypatch.setenv("FBR_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("FBR_MAX_RETRIES_5XX", "3")
    monkeypatch.setenv("MAX_INFLIGHT_PER_SELLER", "1")
    monkeypatch.setenv("MAX_INFLIGHT_TOTAL", "5")
    monkeypatch.setenv("MIDDLEWARE_API_KEYS", f"{API_KEY}, other-system-key")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


@pytest.fixture
def dev_env(env: Settings) -> FbrEnvironment:
    return env.environment("dev")


@pytest.fixture
def prd_env(env: Settings) -> FbrEnvironment:
    return env.environment("prd")


class SleepSpy:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def _make_client(settings: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    from fbr_middleware.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        app.state.pral_client.sleep = SleepSpy()  # no real backoff in tests
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://middleware.test") as client:
            yield client, app


@pytest.fixture
async def client(env: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for c, _app in _make_client(env):
        yield c


@pytest.fixture
async def app_and_client(env: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    async for pair in _make_client(env):
        yield pair


@pytest.fixture
def pral(env: Settings) -> AsyncIterator[respx.MockRouter]:
    """respx router for the fake PRAL host. Tests add routes for validate/post."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest.fixture
def validate_url(dev_env: FbrEnvironment) -> str:
    """PRAL sandbox validate URL (what /api/v1/dev/invoices calls)."""
    return dev_env.validate_url


@pytest.fixture
def post_url(dev_env: FbrEnvironment) -> str:
    return dev_env.post_url
