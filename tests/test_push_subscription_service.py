from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.vapid import encode_key, public_key_bytes
from app.models.push_subscription import PushSubscription
from app.repository.push_subscription import PushSubscriptionRepository
from app.schemas.push_subscription import (
    PushSubscriptionEndpoint,
    PushSubscriptionInput,
    PushSubscriptionKeys,
)
from app.services.push_subscription import (
    PushSubscriptionConflictError,
    PushSubscriptionService,
)

PUBLIC_KEY = encode_key(public_key_bytes(ec.generate_private_key(ec.SECP256R1())))
AUTH_KEY = encode_key(b"fake-auth-secret")
NEW_PUBLIC_KEY = encode_key(public_key_bytes(ec.generate_private_key(ec.SECP256R1())))
NEW_AUTH_KEY = encode_key(b"new-auth-secret!")


def make_input(
    endpoint: str = "https://push.example/subscription/1",
    p256dh: str = PUBLIC_KEY,
    auth: str = AUTH_KEY,
) -> PushSubscriptionInput:
    return PushSubscriptionInput(
        endpoint=endpoint,
        keys=PushSubscriptionKeys(p256dh=p256dh, auth=auth),
    )


async def test_save_creates_subscription() -> None:
    session = AsyncMock(spec=AsyncSession)
    service = PushSubscriptionService(session)
    service._repository = MagicMock(spec=PushSubscriptionRepository)
    service._repository.get_by_endpoint = AsyncMock(return_value=None)
    user_id = uuid4()

    await service.save(user_id, make_input())

    subscription = service._repository.add.call_args.args[0]
    assert isinstance(subscription, PushSubscription)
    assert subscription.user_id == user_id
    assert subscription.endpoint == "https://push.example/subscription/1"
    assert subscription.p256dh == PUBLIC_KEY
    assert subscription.auth == AUTH_KEY
    service._repository.save.assert_awaited_once()


async def test_save_updates_existing_subscription_keys() -> None:
    session = AsyncMock(spec=AsyncSession)
    service = PushSubscriptionService(session)
    service._repository = MagicMock(spec=PushSubscriptionRepository)
    user_id = uuid4()
    subscription = PushSubscription(
        user_id=user_id,
        endpoint="https://push.example/subscription/1",
        p256dh="old-key",
        auth="old-auth",
    )
    service._repository.get_by_endpoint = AsyncMock(return_value=subscription)

    await service.save(user_id, make_input(p256dh=NEW_PUBLIC_KEY, auth=NEW_AUTH_KEY))

    assert subscription.p256dh == NEW_PUBLIC_KEY
    assert subscription.auth == NEW_AUTH_KEY
    service._repository.add.assert_not_called()
    service._repository.save.assert_awaited_once()


async def test_save_rejects_subscription_owned_by_another_user() -> None:
    session = AsyncMock(spec=AsyncSession)
    service = PushSubscriptionService(session)
    service._repository = MagicMock(spec=PushSubscriptionRepository)
    subscription = PushSubscription(
        user_id=uuid4(),
        endpoint="https://push.example/subscription/1",
        p256dh="public-key",
        auth="auth-secret",
    )
    service._repository.get_by_endpoint = AsyncMock(return_value=subscription)

    with pytest.raises(PushSubscriptionConflictError):
        await service.save(uuid4(), make_input())

    service._repository.save.assert_not_awaited()


async def test_save_rolls_back_conflicting_insert() -> None:
    session = AsyncMock(spec=AsyncSession)
    service = PushSubscriptionService(session)
    service._repository = MagicMock(spec=PushSubscriptionRepository)
    service._repository.get_by_endpoint = AsyncMock(return_value=None)
    service._repository.save = AsyncMock(
        side_effect=IntegrityError("insert", {}, Exception("conflict"))
    )

    with pytest.raises(PushSubscriptionConflictError):
        await service.save(uuid4(), make_input())

    session.rollback.assert_awaited_once()


async def test_delete_only_removes_subscription_owned_by_user() -> None:
    session = AsyncMock(spec=AsyncSession)
    service = PushSubscriptionService(session)
    service._repository = MagicMock(spec=PushSubscriptionRepository)
    subscription = PushSubscription(
        user_id=uuid4(),
        endpoint="https://push.example/subscription/1",
        p256dh="public-key",
        auth="auth-secret",
    )
    service._repository.get_by_endpoint = AsyncMock(return_value=subscription)

    await service.delete(uuid4(), subscription.endpoint)

    service._repository.delete.assert_not_awaited()
    service._repository.save.assert_not_awaited()


def test_endpoint_must_be_https_url() -> None:
    with pytest.raises(ValidationError):
        PushSubscriptionEndpoint(endpoint="http://push.example/subscription/1")

    with pytest.raises(ValidationError):
        PushSubscriptionEndpoint(endpoint="https:///subscription/1")


@pytest.mark.parametrize(
    ("p256dh", "auth"),
    [("invalid", AUTH_KEY), (PUBLIC_KEY, "invalid"), ("A" * 87, AUTH_KEY)],
)
def test_subscription_rejects_invalid_encryption_keys(p256dh: str, auth: str) -> None:
    with pytest.raises(ValidationError):
        make_input(p256dh=p256dh, auth=auth)
