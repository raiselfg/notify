from datetime import UTC, datetime
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.dependencies import (
    SESSION_COOKIE_NAME,
    cookie_scheme,
    get_auth_token,
    get_current_user,
    verify_request_origin,
)
from app.api.routers.auth import login, logout, register
from app.core.config import settings
from app.models.user import User
from app.schemas.auth import AuthLogin, AuthRegister
from app.services.auth import AuthService, InvalidCredentialsError, SessionNotExist


@pytest.mark.parametrize("operation", ["register", "login"])
async def test_auth_sets_secure_cookie_without_returning_token(operation: str) -> None:
    service = AsyncMock(spec=AuthService)
    service.register.return_value = "test-session-token"
    service.login.return_value = "test-session-token"
    if operation == "register":
        response = await register(
            AuthRegister(login="alice", password="test-password"), service
        )
        assert response.status_code == 201
    else:
        response = await login(
            AuthLogin(login="alice", password="test-password"), service
        )
        assert response.status_code == 204

    assert response.body == b""
    assert response.headers["cache-control"] == "no-store"
    cookies: SimpleCookie[str] = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    cookie = cookies[SESSION_COOKIE_NAME]
    assert cookie.value == "test-session-token"
    assert cookie["httponly"]
    assert cookie["secure"]
    assert cookie["samesite"] == "lax"
    assert cookie["path"] == "/"
    assert cookie["domain"] == ""
    assert int(cookie["max-age"]) == settings.auth_session_ttl_days * 86400


async def test_failed_login_does_not_issue_cookie() -> None:
    service = AsyncMock(spec=AuthService)
    service.login.side_effect = InvalidCredentialsError
    with pytest.raises(HTTPException) as error:
        await login(AuthLogin(login="alice", password="wrong-password"), service)
    assert error.value.status_code == 401


async def test_session_is_read_from_cookie() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", b"__Host-session=test-session-token")],
        }
    )
    assert await get_auth_token(await cookie_scheme(request)) == "test-session-token"


async def test_bearer_header_does_not_replace_cookie() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer test-session-token")],
        }
    )
    with pytest.raises(HTTPException) as error:
        await get_auth_token(await cookie_scheme(request))
    assert error.value.status_code == 401


async def test_expired_cookie_session_is_rejected() -> None:
    service = AsyncMock(spec=AuthService)
    service.get_current_user.side_effect = SessionNotExist
    with pytest.raises(HTTPException) as error:
        await get_current_user("expired-token", service)
    assert error.value.status_code == 401


async def test_logout_revokes_session_and_expires_cookie() -> None:
    service = AsyncMock(spec=AuthService)
    user = User(id=uuid4(), login="alice", created_at=datetime.now(UTC))
    response = await logout("test-session-token", user, service)
    service.revoke_session.assert_awaited_once_with("test-session-token")
    cookies: SimpleCookie[str] = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    cookie = cookies[SESSION_COOKIE_NAME]
    assert cookie["max-age"] == "0"
    assert cookie["path"] == "/"
    assert cookie["secure"]
    assert cookie["httponly"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("origin", [None, "null", "https://attacker.example"])
async def test_unsafe_requests_reject_untrusted_or_missing_origin(
    method: str,
    origin: str | None,
) -> None:
    headers = [(b"host", b"app.example")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    request = Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": "/auth/login",
            "root_path": "",
            "headers": headers,
            "server": ("app.example", 443),
        }
    )
    with pytest.raises(HTTPException) as error:
        await verify_request_origin(request)
    assert error.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
async def test_same_origin_requests_are_allowed(method: str) -> None:
    request = Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": "/tasks",
            "root_path": "",
            "headers": [(b"host", b"app.example"), (b"origin", b"https://app.example")],
            "server": ("app.example", 443),
        }
    )
    await verify_request_origin(request)
