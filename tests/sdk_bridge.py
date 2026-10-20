"""The OpenAI Python SDK against the gateway in-process: `make_openai_client(app)` returns an `openai.OpenAI` whose HTTP
client forwards every request to the ASGI app through Starlette's `TestClient`, so tests exercise what the SDK actually
sends and how it parses responses and errors, with no network.

The SDK is built on httpx (1.x and 2.x) or on its fork httpx2 (3.x); the bridge uses whichever the installed SDK uses.
Tests that need the SDK call `pytest.importorskip("openai")` through this module, so the suite still runs without it.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient


def sdk() -> tuple[ModuleType, ModuleType]:
    """(openai, the httpx module its clients are built on). Skips the test when the SDK is not installed."""
    openai = pytest.importorskip("openai")
    base = next(c for c in openai.DefaultHttpxClient.__mro__[1:] if c.__name__ == "Client")
    return openai, importlib.import_module(base.__module__.split(".")[0])


def make_openai_client(app: Any, *, calls: list[Any] | None = None, max_retries: int = 0, **kwargs: Any) -> Any:
    """An `openai.OpenAI` client for `app`. `calls` (when given) receives every request the SDK sends, retries
    included. `max_retries` defaults to 0 so a 429 or 5xx fails at once instead of backing off."""
    openai, hx = sdk()
    app_client = TestClient(app, raise_server_exceptions=False)

    def forward(request: Any) -> Any:
        if calls is not None:
            calls.append(request)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        r = app_client.request(request.method, str(request.url), headers=headers, content=request.read())
        keep = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in ("content-length", "content-encoding")]
        return hx.Response(r.status_code, headers=keep, content=r.content, request=request)

    kwargs.setdefault("api_key", "unused")
    return openai.OpenAI(
        base_url="http://testserver/v1",
        http_client=hx.Client(transport=hx.MockTransport(forward)),
        max_retries=max_retries,
        **kwargs,
    )
