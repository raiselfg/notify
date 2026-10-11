from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_subscription import PushSubscription
from app.repository.push_subscription import PushSubscriptionRepository
from app.schemas.push_subscription import (
    PushSubscriptionEndpoint,
    PushSubscriptionInput,
)
from app.services.push_subscription import (
    PushSubscriptionConflictError,
    PushSubscriptionService,
)


def make_input(
    endpoint: str = "https://push.example/subscription/1",
    p256dh: str = "public-key",
    auth: str = "auth-secret",
) -> PushSubscriptionInput:
    return PushSubscriptionInput(
        endpoint=endpoint,
        keys={"p256dh": p256dh, "auth": auth},
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
    assert subscription.p256dh == "public-key"
    assert subscription.auth == "auth-secret"
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

    await service.save(user_id, make_input(p256dh="new-key", auth="new-auth"))

    assert subscription.p256dh == "new-key"
    assert subscription.auth == "new-auth"
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
