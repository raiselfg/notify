from typing import cast

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from starlette.types import Message

from app.api.dependencies import verify_request_origin
from app.core.config import Settings, settings
from app.main import app


async def preflight(origin: str, method: str = "PATCH") -> tuple[int, dict[str, str]]:
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "OPTIONS",
            "scheme": "http",
            "path": "/api/tasks",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"localhost:8000"),
                (b"origin", origin.encode()),
                (b"access-control-request-method", method.encode()),
                (b"access-control-request-headers", b"content-type"),
            ],
            "server": ("localhost", 8000),
        },
        receive,
        send,
    )
    response = messages[0]
    headers = cast(list[tuple[bytes, bytes]], response["headers"])
    return int(response["status"]), {
        key.decode(): value.decode() for key, value in headers
    }


async def test_allowed_frontend_can_preflight_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    code, headers = await preflight("http://localhost:3000")
    assert code == 200
    assert headers["access-control-allow-origin"] == "http://localhost:3000"
    assert headers["access-control-allow-credentials"] == "true"
    assert "PATCH" in headers["access-control-allow-methods"]
    assert "Origin" in headers["vary"]


async def test_untrusted_origin_cannot_preflight() -> None:
    code, headers = await preflight("https://attacker.example")
    assert code == 400
    assert "access-control-allow-origin" not in headers


async def test_unlisted_method_cannot_preflight() -> None:
    code, _ = await preflight("http://localhost:3000", "TRACE")
    assert code == 400


@pytest.mark.parametrize("origin", ["http://localhost:3000", "https://app.example"])
async def test_explicit_frontend_origin_passes_csrf_check(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    monkeypatch.setattr(settings, "cors_origins", [origin])
    await verify_request_origin(
        Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "http",
                "path": "/api/auth/login",
                "headers": [(b"host", b"localhost:8000"), (b"origin", origin.encode())],
            }
        )
    )


async def test_similar_origin_is_not_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cors_origins", ["https://app.example"])
    with pytest.raises(HTTPException) as error:
        await verify_request_origin(
            Request(
                {
                    "type": "http",
                    "method": "POST",
                    "scheme": "https",
                    "path": "/api/tasks",
                    "headers": [
                        (b"host", b"api.example"),
                        (b"origin", b"https://app.example.attacker.example"),
                    ],
                }
            )
        )
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "null",
        "https://app.example/",
        "https://user:pass@app.example",
        "https://app.example/path",
    ],
)
def test_invalid_origins_are_rejected(origin: str) -> None:
    with pytest.raises(ValidationError, match="CORS origins"):
        Settings.model_validate({**settings.model_dump(), "cors_origins": [origin]})
