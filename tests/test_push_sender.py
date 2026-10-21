import asyncio
import json
import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import aiohttp
import http_ece
import pytest
from aiohttp.abc import ResolveResult
from aiohttp.resolver import ThreadedResolver
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02

from app.core.config import Settings
from app.core.push_endpoint import (
    PublicPushResolver,
    UnsafePushEndpointError,
    push_audience,
)
from app.core.vapid import encode_key, public_key_bytes
from app.schemas.push import PushMessage
from app.schemas.push_subscription import PushSubscriptionInput, PushSubscriptionKeys
from app.services.push import PushSender, PushStatus, parse_retry_after


@pytest.fixture
def sender_settings() -> Settings:
    key = ec.generate_private_key(ec.SECP256R1())
    return Settings.model_validate(
        {
            "db_host": "localhost",
            "db_port": 5432,
            "db_name": "test",
            "db_user": "test",
            "db_password": "fake-password",
            "auth_session_ttl_days": 30,
            "vapid_private_key": encode_key(
                key.private_numbers().private_value.to_bytes(32, "big")
            ),
            "vapid_public_key": encode_key(public_key_bytes(key)),
            "vapid_subject": "mailto:admin@example.com",
        }
    )


@pytest.fixture
def sender(sender_settings: Settings) -> PushSender:
    return PushSender(sender_settings)


@pytest.fixture
def receiver_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def subscription(receiver_key: ec.EllipticCurvePrivateKey) -> PushSubscriptionInput:
    return PushSubscriptionInput(
        endpoint="https://push.example/subscription/1",
        keys=PushSubscriptionKeys(
            p256dh=encode_key(public_key_bytes(receiver_key)),
            auth=encode_key(b"fake-auth-secret"),
        ),
    )


@pytest.fixture
def message() -> PushMessage:
    return PushMessage(
        event_id=uuid4(), task_id=uuid4(), title="Перерыв", body="Отдохни"
    )


def mock_http(monkeypatch: pytest.MonkeyPatch, code: int) -> MagicMock:
    response = MagicMock()
    response.status = code
    response.headers = {"Retry-After": "120"}
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    client = MagicMock()
    client.post.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    monkeypatch.setattr(aiohttp, "ClientSession", MagicMock(return_value=context))
    monkeypatch.setattr(aiohttp, "TCPConnector", MagicMock())
    return client


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (201, PushStatus.ACCEPTED),
        (202, PushStatus.ACCEPTED),
        (404, PushStatus.INVALID_SUBSCRIPTION),
        (410, PushStatus.INVALID_SUBSCRIPTION),
        (429, PushStatus.RETRY),
        (503, PushStatus.RETRY),
        (401, PushStatus.FAILED),
        (403, PushStatus.FAILED),
        (413, PushStatus.FAILED),
        (307, PushStatus.FAILED),
    ],
)
async def test_response_and_encrypted_message(
    monkeypatch: pytest.MonkeyPatch,
    sender: PushSender,
    subscription: PushSubscriptionInput,
    receiver_key: ec.EllipticCurvePrivateKey,
    message: PushMessage,
    code: int,
    status: PushStatus,
) -> None:
    client = mock_http(monkeypatch, code)
    result = await sender.send(
        subscription, message, datetime.now(UTC) + timedelta(seconds=90)
    )
    assert result.status == status
    assert result.status_code == code
    assert result.retry_after_seconds == (120 if status == PushStatus.RETRY else None)
    kwargs = client.post.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert 1 <= int(kwargs["headers"]["TTL"]) <= 90
    assert kwargs["headers"]["Content-Encoding"] == "aes128gcm"
    assert Vapid02.verify(kwargs["headers"]["Authorization"])
    decrypted = http_ece.decrypt(
        kwargs["data"],
        private_key=receiver_key,
        auth_secret=b"fake-auth-secret",
        version="aes128gcm",
    )
    assert json.loads(decrypted) == message.model_dump(mode="json")


async def test_expired_message_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
    sender: PushSender,
    subscription: PushSubscriptionInput,
    message: PushMessage,
) -> None:
    client = mock_http(monkeypatch, 201)
    result = await sender.send(subscription, message, datetime.now(UTC))
    assert result.status == PushStatus.EXPIRED
    client.post.assert_not_called()


@pytest.mark.parametrize("error", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_network_failure_requests_retry(
    monkeypatch: pytest.MonkeyPatch,
    sender: PushSender,
    subscription: PushSubscriptionInput,
    message: PushMessage,
    error: Exception,
) -> None:
    client = mock_http(monkeypatch, 201)
    client.post.return_value.__aenter__.side_effect = error
    result = await sender.send(
        subscription, message, datetime.now(UTC) + timedelta(minutes=1)
    )
    assert result.status == PushStatus.RETRY
    assert result.reason in {"timeout", "network_error"}


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1/push",
        "https://127.1/push",
        "https://2130706433/push",
        "https://10.0.0.1/push",
        "https://[::1]/push",
        "https://169.254.169.254/push",
        "https://push.example/push#fragment",
        "https://push.example/\\bad",
        "https://push.example/\nbad",
        "https://push.example:bad/push",
    ],
)
def test_unsafe_endpoints_are_rejected(endpoint: str) -> None:
    with pytest.raises(ValueError):
        push_audience(endpoint)


def test_https_ports_and_origin_normalization() -> None:
    assert (
        push_audience("https://push.example:8443/push") == "https://push.example:8443"
    )
    assert push_audience("https://PUSH.EXAMPLE:443/push") == "https://push.example"


async def test_dns_addresses_are_checked_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = PublicPushResolver()
    addresses: list[ResolveResult] = [
        {
            "hostname": "push.example",
            "host": "10.0.0.1",
            "port": 443,
            "family": socket.AF_INET,
            "proto": 0,
            "flags": 0,
        }
    ]
    monkeypatch.setattr(ThreadedResolver, "resolve", AsyncMock(return_value=addresses))
    with pytest.raises(UnsafePushEndpointError):
        await resolver.resolve("push.example", 443)
    await resolver.close()


def test_retry_after_formats() -> None:
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    assert parse_retry_after("60", now) == 60
    assert parse_retry_after("Wed, 21 Oct 2026 12:02:00 GMT", now) == 120
    assert parse_retry_after("Wed, 21 Oct 2026 11:00:00 GMT", now) == 0
    assert parse_retry_after("invalid", now) is None
    assert parse_retry_after("-1", now) is None
    assert parse_retry_after("9" * 5000, now) is None


async def test_timeout_bounds_request(
    monkeypatch: pytest.MonkeyPatch,
    sender_settings: Settings,
    sender: PushSender,
    subscription: PushSubscriptionInput,
    message: PushMessage,
) -> None:
    client = mock_http(monkeypatch, 201)
    monkeypatch.setattr(sender_settings, "push_timeout_seconds", 0.05)

    async def slow_response() -> None:
        await asyncio.sleep(1)

    client.post.return_value.__aenter__.side_effect = slow_response
    result = await sender.send(
        subscription, message, datetime.now(UTC) + timedelta(minutes=1)
    )
    assert result.status == PushStatus.RETRY
    assert result.reason == "timeout"


async def test_concurrency_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    sender_settings: Settings,
    subscription: PushSubscriptionInput,
    message: PushMessage,
) -> None:
    monkeypatch.setattr(sender_settings, "push_max_concurrency", 2)
    sender = PushSender(sender_settings)
    client = mock_http(monkeypatch, 201)
    active = 0
    peak = 0

    async def response() -> MagicMock:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        result = MagicMock()
        result.status = 201
        return result

    client.post.return_value.__aenter__.side_effect = response
    results = await asyncio.gather(
        *(
            sender.send(subscription, message, datetime.now(UTC) + timedelta(minutes=1))
            for _ in range(5)
        )
    )
    assert peak == 2
    assert all(result.status == PushStatus.ACCEPTED for result in results)


async def test_cancelled_send_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    sender: PushSender,
    subscription: PushSubscriptionInput,
    message: PushMessage,
) -> None:
    client = mock_http(monkeypatch, 201)
    client.post.return_value.__aenter__.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await sender.send(
            subscription, message, datetime.now(UTC) + timedelta(minutes=1)
        )


async def test_dns_rejection_reaches_sender_without_http_request(
    monkeypatch: pytest.MonkeyPatch,
    sender: PushSender,
    subscription: PushSubscriptionInput,
    message: PushMessage,
) -> None:
    addresses: list[ResolveResult] = [
        {
            "hostname": "push.example",
            "host": "127.0.0.1",
            "port": 443,
            "family": socket.AF_INET,
            "proto": 0,
            "flags": 0,
        }
    ]
    monkeypatch.setattr(ThreadedResolver, "resolve", AsyncMock(return_value=addresses))
    result = await sender.send(
        subscription, message, datetime.now(UTC) + timedelta(minutes=1)
    )
    assert result.status == PushStatus.FAILED
    assert result.reason == "unsafe_endpoint"


async def test_private_ip_does_not_reach_http(
    monkeypatch: pytest.MonkeyPatch,
    sender: PushSender,
    subscription: PushSubscriptionInput,
    message: PushMessage,
) -> None:
    client = mock_http(monkeypatch, 201)
    subscription = subscription.model_copy(
        update={"endpoint": "https://127.0.0.1/push"}
    )
    result = await sender.send(
        subscription, message, datetime.now(UTC) + timedelta(minutes=1)
    )
    assert result.status == PushStatus.FAILED
    client.post.assert_not_called()
