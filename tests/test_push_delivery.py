from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.vapid import encode_key, public_key_bytes
from app.db.base import Base
from app.models.push_subscription import PushSubscription
from app.schemas.push import PushMessage
from app.services.push import PushResult, PushSender, PushStatus
from app.services.push_delivery import PushDeliveryService


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def make_subscription() -> PushSubscription:
    return PushSubscription(
        id=uuid4(),
        user_id=uuid4(),
        endpoint="https://push.example/subscription/1",
        p256dh=encode_key(public_key_bytes(ec.generate_private_key(ec.SECP256R1()))),
        auth=encode_key(b"fake-auth-secret"),
        updated_at=datetime.now(UTC),
    )


def async_session(db: Session) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = db.execute
    session.flush.side_effect = db.flush
    session.commit.side_effect = db.commit
    session.rollback.side_effect = db.rollback
    return session


@pytest.mark.parametrize("changed", [False, True])
async def test_invalid_subscription_removed_only_if_unchanged(
    db: Session, changed: bool
) -> None:
    target = make_subscription()
    db.add(target)
    db.commit()
    sender = AsyncMock(spec=PushSender)

    async def send(*args: object) -> PushResult:
        assert not db.in_transaction()
        if changed:
            db.execute(
                update(PushSubscription)
                .where(PushSubscription.id == target.id)
                .values(auth=encode_key(b"new-auth-secret!"))
            )
            db.commit()
        return PushResult(PushStatus.INVALID_SUBSCRIPTION, 410)

    sender.send.side_effect = send
    results = await PushDeliveryService(async_session(db), sender).send_for_user(
        target.user_id,
        PushMessage(event_id=uuid4(), task_id=uuid4(), title="Break", body="Rest"),
        datetime.now(UTC) + timedelta(minutes=1),
    )
    assert results[target.id].status == PushStatus.INVALID_SUBSCRIPTION
    remaining = db.scalar(
        select(PushSubscription).where(PushSubscription.id == target.id)
    )
    assert (remaining is not None) == changed


async def test_only_owner_receives_message_and_failures_are_independent(
    db: Session,
) -> None:
    first = make_subscription()
    second = make_subscription()
    second.user_id = first.user_id
    second.endpoint = "https://push.example/subscription/2"
    other = make_subscription()
    other.endpoint = "https://push.example/subscription/other"
    db.add_all([first, second, other])
    db.commit()
    sender = AsyncMock(spec=PushSender)
    sender.send.side_effect = [
        PushResult(PushStatus.ACCEPTED, 201),
        PushResult(PushStatus.RETRY, 503),
    ]
    results = await PushDeliveryService(async_session(db), sender).send_for_user(
        first.user_id,
        PushMessage(event_id=uuid4(), task_id=uuid4(), title="Break", body="Rest"),
        datetime.now(UTC) + timedelta(minutes=1),
    )
    assert set(results) == {first.id, second.id}
    assert {result.status for result in results.values()} == {
        PushStatus.ACCEPTED,
        PushStatus.RETRY,
    }
    assert sender.send.await_count == 2
    assert (
        db.scalar(select(PushSubscription).where(PushSubscription.id == other.id))
        is not None
    )


async def test_no_subscriptions_does_not_send(db: Session) -> None:
    sender = AsyncMock(spec=PushSender)
    results = await PushDeliveryService(async_session(db), sender).send_for_user(
        uuid4(),
        PushMessage(event_id=uuid4(), task_id=uuid4(), title="Break", body="Rest"),
        datetime.now(UTC) + timedelta(minutes=1),
    )
    assert results == {}
    sender.send.assert_not_awaited()


async def test_cleanup_failure_rolls_back(db: Session) -> None:
    target = make_subscription()
    db.add(target)
    db.commit()
    sender = AsyncMock(spec=PushSender)
    sender.send.return_value = PushResult(PushStatus.INVALID_SUBSCRIPTION, 410)
    session = async_session(db)
    session.execute.side_effect = [
        db.execute(
            select(PushSubscription).where(PushSubscription.user_id == target.user_id)
        ),
        RuntimeError("fake database failure"),
    ]
    with pytest.raises(RuntimeError, match="fake database failure"):
        await PushDeliveryService(session, sender).send_for_user(
            target.user_id,
            PushMessage(event_id=uuid4(), task_id=uuid4(), title="Break", body="Rest"),
            datetime.now(UTC) + timedelta(minutes=1),
        )
    session.rollback.assert_awaited_once()
    assert db.get(PushSubscription, target.id) is not None
