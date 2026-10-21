import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from math import ceil

import aiohttp
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02
from pywebpush import WebPusher, WebPushException

from app.core.config import Settings
from app.core.push_endpoint import (
    PublicPushResolver,
    UnsafePushEndpointError,
    push_audience,
)
from app.core.vapid import decode_key
from app.schemas.push import PushMessage
from app.schemas.push_subscription import PushSubscriptionInput


class PushStatus(StrEnum):
    ACCEPTED = "accepted"
    INVALID_SUBSCRIPTION = "invalid_subscription"
    RETRY = "retry"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PushResult:
    status: PushStatus
    status_code: int | None = None
    retry_after_seconds: int | None = None
    reason: str | None = None


def parse_retry_after(value: str | None, now: datetime) -> int | None:
    if value is None:
        return None
    try:
        if value.isascii() and value.isdigit():
            return int(value)
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0, ceil((date - now).total_seconds()))
    except ValueError, TypeError, OverflowError:
        return None


class PushSender:
    def __init__(self, settings: Settings) -> None:
        settings.require_vapid()
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.push_max_concurrency)

    def _encode(
        self, subscription: PushSubscriptionInput, message: PushMessage, audience: str
    ) -> tuple[bytes, dict[str, str]]:
        private = self._settings.vapid_private_key
        subject = self._settings.vapid_subject
        if private is None or subject is None:
            raise ValueError("VAPID is not configured")
        key = ec.derive_private_key(
            int.from_bytes(decode_key(private.get_secret_value(), 32), "big"),
            ec.SECP256R1(),
        )
        headers = Vapid02(key).sign(
            {
                "aud": audience,
                "sub": subject,
                "exp": int(datetime.now(UTC).timestamp()) + 12 * 3600,
            }
        )
        payload = message.model_dump_json().encode()
        if len(payload) > 3993:
            raise ValueError("Push payload is too large")
        body = WebPusher(
            {
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.keys.p256dh,
                    "auth": subscription.keys.auth,
                },
            }
        ).encode(payload)["body"]
        headers.update(
            {
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
            }
        )
        return body, headers

    async def _post(
        self, endpoint: str, body: bytes, headers: dict[str, str]
    ) -> PushResult:
        resolver = PublicPushResolver()
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=resolver), trust_env=False
            ) as client:
                async with client.post(
                    endpoint, data=body, headers=headers, allow_redirects=False
                ) as response:
                    code = response.status
                    if 200 <= code <= 202:
                        return PushResult(PushStatus.ACCEPTED, code)
                    if code in (404, 410):
                        return PushResult(PushStatus.INVALID_SUBSCRIPTION, code)
                    if code == 429 or 500 <= code < 600:
                        return PushResult(
                            PushStatus.RETRY,
                            code,
                            parse_retry_after(
                                response.headers.get("Retry-After"), datetime.now(UTC)
                            ),
                        )
                    return PushResult(PushStatus.FAILED, code)
        finally:
            await resolver.close()

    async def send(
        self,
        subscription: PushSubscriptionInput,
        message: PushMessage,
        expires_at: datetime,
    ) -> PushResult:
        if expires_at.tzinfo is None:
            raise ValueError("Push expiration must include a timezone")
        async with self._semaphore:
            if (expires_at - datetime.now(UTC)).total_seconds() < 1:
                return PushResult(PushStatus.EXPIRED)
            try:
                audience = push_audience(subscription.endpoint)
                async with asyncio.timeout(self._settings.push_timeout_seconds):
                    body, headers = await asyncio.to_thread(
                        self._encode, subscription, message, audience
                    )
                    ttl = min(
                        self._settings.push_ttl_seconds,
                        int((expires_at - datetime.now(UTC)).total_seconds()),
                    )
                    if ttl < 1:
                        return PushResult(PushStatus.EXPIRED)
                    headers["TTL"] = str(ttl)
                    return await self._post(subscription.endpoint, body, headers)
            except UnsafePushEndpointError:
                return PushResult(PushStatus.FAILED, reason="unsafe_endpoint")
            except TimeoutError:
                return PushResult(PushStatus.RETRY, reason="timeout")
            except aiohttp.ClientError:
                return PushResult(PushStatus.RETRY, reason="network_error")
            except ValueError, WebPushException:
                return PushResult(PushStatus.FAILED, reason="invalid_push_data")
