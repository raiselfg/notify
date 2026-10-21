import asyncio
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import Connection, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.core.vapid import encode_key, public_key_bytes
from app.db.base import Base
from app.models.push_delivery import PushDelivery, PushEvent
from app.models.push_subscription import PushSubscription
from app.models.task import ReminderTask
from app.models.user import User
from app.repository.push_queue import PushQueueRepository
from app.schemas.task import TaskUpdate
from app.services.push import PushResult, PushSender, PushStatus
from app.services.push_queue import DeliveryClaim, PreparedDelivery, PushQueueService
from app.services.task import TaskService
from app.worker import run_cycle

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def migrate(connection: Connection) -> None:
    context = MigrationContext.configure(connection)
    revisions = list(
        ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions()
    )
    with Operations.context(context):
        for revision in reversed(revisions):
            cast(Callable[[], None], revision.module.upgrade)()


@pytest.fixture
def config() -> Settings:
    return Settings.model_validate(
        {
            "db_host": "localhost",
            "db_port": 5432,
            "db_name": "test",
            "db_user": "test",
            "db_password": "fake-password",
            "auth_session_ttl_days": 30,
            "vapid_private_key": None,
            "vapid_public_key": None,
            "vapid_subject": None,
        }
    )


@pytest.fixture
async def queue_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL queue integration tests")
    schema = "test_push_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(
        url,
        connect_args={"server_settings": {"search_path": schema, "timezone": "UTC"}},
    )
    try:
        async with admin.begin() as connection:
            await connection.execute(CreateSchema(schema))
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await admin.dispose()


async def seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    due: datetime = NOW,
    subscriptions: int = 1,
) -> tuple[UUID, UUID, list[UUID]]:
    user_id, task_id = uuid4(), uuid4()
    ids = [uuid4() for _ in range(subscriptions)]
    async with factory() as session, session.begin():
        session.add(User(id=user_id, login="test-user", password_hash="fake-hash"))
        await session.flush()
        session.add(
            ReminderTask(
                id=task_id,
                user_id=user_id,
                title="Take a break",
                interval_minutes=30,
                next_reminder_at=due,
            )
        )
        for subscription_id in ids:
            session.add(
                PushSubscription(
                    id=subscription_id,
                    user_id=user_id,
                    endpoint=f"https://push.example/{subscription_id}",
                    p256dh=encode_key(
                        public_key_bytes(ec.generate_private_key(ec.SECP256R1()))
                    ),
                    auth=encode_key(b"fake-auth-secret"),
                )
            )
    return user_id, task_id, ids


async def enqueue(
    factory: async_sessionmaker[AsyncSession], config: Settings, now: datetime = NOW
) -> int:
    async with factory() as session:
        return await PushQueueService(session, config).enqueue_due(now)


async def claim(
    factory: async_sessionmaker[AsyncSession], config: Settings, now: datetime = NOW
) -> list[DeliveryClaim]:
    async with factory() as session:
        return await PushQueueService(session, config).claim_ready(now)


async def prepare(
    factory: async_sessionmaker[AsyncSession],
    config: Settings,
    value: DeliveryClaim,
    now: datetime = NOW,
) -> PreparedDelivery | None:
    async with factory() as session:
        return await PushQueueService(session, config).prepare(value, now)


async def record(
    factory: async_sessionmaker[AsyncSession],
    config: Settings,
    value: PreparedDelivery,
    result: PushResult,
    now: datetime = NOW,
) -> bool:
    async with factory() as session:
        return await PushQueueService(session, config).record_result(value, result, now)


async def test_migrations_match_models(
    queue_db: async_sessionmaker[AsyncSession],
) -> None:
    async with queue_db() as session:
        connection = await session.connection()
        differences = await connection.run_sync(
            lambda conn: compare_metadata(
                MigrationContext.configure(conn), Base.metadata
            )
        )
        assert differences == []


@pytest.mark.parametrize(
    "now", [NOW + timedelta(minutes=70), NOW + timedelta(minutes=90)]
)
async def test_downtime_collapses_to_one_event_and_keeps_schedule(
    queue_db: async_sessionmaker[AsyncSession], config: Settings, now: datetime
) -> None:
    _, task_id, _ = await seed(queue_db, due=NOW, subscriptions=2)
    assert await enqueue(queue_db, config, now) == 1
    assert await enqueue(queue_db, config, now) == 0
    async with queue_db() as session:
        task = await session.get(ReminderTask, task_id)
        assert task is not None
        expected = NOW + timedelta(minutes=90 if now.minute == 10 else 120)
        assert task.next_reminder_at == expected
        assert await session.scalar(select(func.count()).select_from(PushEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(PushDelivery)) == 2


async def test_no_subscriptions_moves_schedule_without_backlog(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    await seed(queue_db, subscriptions=0)
    assert await enqueue(queue_db, config) == 1
    assert await claim(queue_db, config) == []
    async with queue_db() as session:
        event = await session.scalar(select(PushEvent))
        assert event is not None and event.no_subscriptions
    assert await enqueue(queue_db, config) == 0


async def test_scheduler_skips_locked_task_and_workers_claim_distinct_rows(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    _, task_id, _ = await seed(queue_db, subscriptions=4)
    async with queue_db() as locked, locked.begin():
        await locked.scalar(
            select(ReminderTask).where(ReminderTask.id == task_id).with_for_update()
        )
        assert await asyncio.wait_for(enqueue(queue_db, config), timeout=3) == 0
    assert (
        sum(await asyncio.gather(enqueue(queue_db, config), enqueue(queue_db, config)))
        == 1
    )
    config.push_max_concurrency = 2
    first, second = await asyncio.gather(
        claim(queue_db, config), claim(queue_db, config)
    )
    assert len(first) == len(second) == 2
    assert {item.id for item in first}.isdisjoint(item.id for item in second)


async def test_restart_reclaims_lease_and_rejects_old_ack(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    await seed(queue_db)
    await enqueue(queue_db, config)
    original = (await claim(queue_db, config))[0]
    prepared = await prepare(queue_db, config, original)
    assert prepared is not None
    assert await claim(queue_db, config, NOW + timedelta(seconds=20)) == []
    later = NOW + timedelta(seconds=41)
    replacement = (await claim(queue_db, config, later))[0]
    assert replacement.id == original.id and replacement.token != original.token
    assert not await record(
        queue_db, config, prepared, PushResult(PushStatus.ACCEPTED, 201), later
    )
    restarted = await prepare(queue_db, config, replacement, later)
    assert restarted is not None
    assert restarted.message.event_id == prepared.message.event_id
    assert await record(
        queue_db, config, restarted, PushResult(PushStatus.ACCEPTED, 201), later
    )
    assert await claim(queue_db, config, later + timedelta(seconds=41)) == []


async def test_partial_failure_retries_only_failed_device(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    await seed(queue_db, subscriptions=2)
    await enqueue(queue_db, config)
    claims = await claim(queue_db, config)
    first = await prepare(queue_db, config, claims[0])
    second = await prepare(queue_db, config, claims[1])
    assert first is not None and second is not None
    await record(queue_db, config, first, PushResult(PushStatus.ACCEPTED, 201))
    await record(queue_db, config, second, PushResult(PushStatus.RETRY, 429, 120))
    assert await claim(queue_db, config, NOW + timedelta(seconds=119)) == []
    retries = await claim(queue_db, config, NOW + timedelta(seconds=120))
    assert len(retries) == 1 and retries[0].id == second.claim.id


@pytest.mark.parametrize("operation", ["complete", "interval", "delete", "unsubscribe"])
async def test_task_and_subscription_changes_cancel_delivery(
    queue_db: async_sessionmaker[AsyncSession], config: Settings, operation: str
) -> None:
    user_id, task_id, subscriptions = await seed(queue_db)
    await enqueue(queue_db, config)
    value = (await claim(queue_db, config))[0]
    async with queue_db() as session:
        service = TaskService(session)
        if operation == "complete":
            await service.complete(user_id, task_id)
        elif operation == "interval":
            await service.update(user_id, task_id, TaskUpdate(interval_minutes=60))
        elif operation == "delete":
            await service.delete(user_id, task_id)
        else:
            subscription = await session.get(PushSubscription, subscriptions[0])
            assert subscription is not None
            await session.delete(subscription)
            await session.commit()
    assert await prepare(queue_db, config, value) is None
    async with queue_db() as session:
        delivery = await session.get(PushDelivery, value.id)
        assert delivery is None or delivery.status == "cancelled"


@pytest.mark.parametrize("change", [False, True])
async def test_invalid_subscription_cleanup_preserves_refreshed_keys(
    queue_db: async_sessionmaker[AsyncSession], config: Settings, change: bool
) -> None:
    _, _, subscriptions = await seed(queue_db)
    await enqueue(queue_db, config)
    value = await prepare(queue_db, config, (await claim(queue_db, config))[0])
    assert value is not None
    if change:
        async with queue_db() as session, session.begin():
            await session.execute(
                update(PushSubscription)
                .where(PushSubscription.id == subscriptions[0])
                .values(auth=encode_key(b"new-auth-secret!"))
            )
    await record(
        queue_db, config, value, PushResult(PushStatus.INVALID_SUBSCRIPTION, 410)
    )
    async with queue_db() as session:
        subscription = await session.get(PushSubscription, subscriptions[0])
        assert (subscription is not None) == change
        delivery = await session.get(PushDelivery, value.claim.id)
        assert delivery is not None and delivery.status == "invalid_subscription"


@pytest.mark.parametrize("outcome", ["expires", "max_attempts", "auth_failure"])
async def test_retry_limits_and_terminal_failures(
    queue_db: async_sessionmaker[AsyncSession], config: Settings, outcome: str
) -> None:
    await seed(queue_db)
    if outcome == "max_attempts":
        config.push_max_attempts = 1
    await enqueue(queue_db, config)
    value = await prepare(queue_db, config, (await claim(queue_db, config))[0])
    assert value is not None
    result = (
        PushResult(PushStatus.FAILED, 403)
        if outcome == "auth_failure"
        else PushResult(PushStatus.RETRY, 503, 10**30 if outcome == "expires" else None)
    )
    await record(queue_db, config, value, result)
    async with queue_db() as session:
        delivery = await session.get(PushDelivery, value.claim.id)
        assert delivery is not None
        assert delivery.status == ("expired" if outcome == "expires" else "failed")
        assert (
            await session.scalar(select(func.count()).select_from(PushSubscription))
            == 1
        )


async def test_worker_cycle_sends_outside_database_transaction(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    now = datetime.now(UTC)
    _, task_id, _ = await seed(queue_db, due=now - timedelta(seconds=1))
    sender = AsyncMock(spec=PushSender)

    async def send(*args: object) -> PushResult:
        async with queue_db() as session, session.begin():
            # Both locks must be available while the network send is in progress.
            await session.execute(text("SET LOCAL lock_timeout = '1s'"))
            await session.scalar(
                select(ReminderTask).where(ReminderTask.id == task_id).with_for_update()
            )
            await session.scalar(select(PushDelivery).with_for_update())
        return PushResult(PushStatus.ACCEPTED, 201)

    sender.send.side_effect = send
    assert await run_cycle(queue_db, sender, config)
    sender.send.assert_awaited_once()
    async with queue_db() as session:
        delivery = await session.scalar(select(PushDelivery))
        assert delivery is not None and delivery.status == "accepted"


async def test_worker_skips_delivery_locked_by_other_transaction(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    await seed(queue_db, subscriptions=2)
    await enqueue(queue_db, config)
    async with queue_db() as session, session.begin():
        locked = await PushQueueRepository(session).get_ready_deliveries(NOW, 1)
        assert len(locked) == 1
        claims = await asyncio.wait_for(claim(queue_db, config), timeout=3)
        assert len(claims) == 1 and claims[0].id != locked[0][0].id


async def test_enqueue_failure_rolls_back_event_and_schedule(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    from sqlalchemy import event

    _, task_id, _ = await seed(queue_db)
    async with queue_db() as session:

        def fail_commit(*args: object) -> None:
            raise RuntimeError("fake commit failure")

        event.listen(session.sync_session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="fake commit failure"):
            await PushQueueService(session, config).enqueue_due(NOW)
        event.remove(session.sync_session, "before_commit", fail_commit)
    async with queue_db() as session:
        task = await session.get(ReminderTask, task_id)
        assert task is not None and task.next_reminder_at == NOW
        assert await session.scalar(select(func.count()).select_from(PushEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(PushDelivery)) == 0


async def test_migration_preserves_existing_tasks(
    queue_db: async_sessionmaker[AsyncSession],
) -> None:
    user_id, task_id = uuid4(), uuid4()

    def upgrade_existing(connection: Connection) -> None:
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        revision = script.get_revision("b74c91e2a6d0")
        assert revision is not None
        with Operations.context(MigrationContext.configure(connection)):
            cast(Callable[[], None], revision.module.downgrade)()
            connection.execute(
                text(
                    "INSERT INTO users (id, login, password_hash) "
                    "VALUES (:id, :login, :hash)"
                ),
                {"id": user_id, "login": "legacy-user", "hash": "fake-hash"},
            )
            connection.execute(
                text(
                    "INSERT INTO tasks "
                    "(id, user_id, title, interval_minutes, next_reminder_at) "
                    "VALUES (:id, :user_id, :title, :interval, :due)"
                ),
                {
                    "id": task_id,
                    "user_id": user_id,
                    "title": "Existing task",
                    "interval": 30,
                    "due": NOW,
                },
            )
            cast(Callable[[], None], revision.module.upgrade)()

    async with queue_db() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(upgrade_existing)
    async with queue_db() as session:
        task = await session.get(ReminderTask, task_id)
        assert task is not None
        assert task.title == "Existing task"
        assert task.next_reminder_at == NOW
        assert task.schedule_version == 0


async def test_title_update_preserves_queued_message_and_schedule(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    user_id, task_id, _ = await seed(queue_db)
    await enqueue(queue_db, config)
    async with queue_db() as session:
        task = await TaskService(session).update(
            user_id, task_id, TaskUpdate(title="New title")
        )
        assert task.next_reminder_at == NOW + timedelta(minutes=30)
    value = await prepare(queue_db, config, (await claim(queue_db, config))[0])
    assert value is not None and value.message.title == "Take a break"


async def test_expired_event_is_not_claimed(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    await seed(queue_db)
    await enqueue(queue_db, config)
    assert await claim(queue_db, config, NOW + timedelta(hours=1)) == []
    async with queue_db() as session:
        delivery = await session.scalar(select(PushDelivery))
        assert delivery is not None and delivery.status == "expired"


async def test_crashed_attempts_are_bounded(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    config.push_max_attempts = 1
    await seed(queue_db)
    await enqueue(queue_db, config)
    assert len(await claim(queue_db, config)) == 1
    assert await claim(queue_db, config, NOW + timedelta(seconds=41)) == []
    async with queue_db() as session:
        delivery = await session.scalar(select(PushDelivery))
        assert delivery is not None and delivery.status == "failed"
        assert delivery.last_reason == "attempt_limit"


async def test_late_ack_is_rejected_even_without_another_worker(
    queue_db: async_sessionmaker[AsyncSession], config: Settings
) -> None:
    await seed(queue_db)
    await enqueue(queue_db, config)
    value = await prepare(queue_db, config, (await claim(queue_db, config))[0])
    assert value is not None
    assert not await record(
        queue_db,
        config,
        value,
        PushResult(PushStatus.ACCEPTED, 201),
        NOW + timedelta(seconds=41),
    )
    assert len(await claim(queue_db, config, NOW + timedelta(seconds=41))) == 1


async def test_worker_drains_current_attempt_on_stop(
    queue_db: async_sessionmaker[AsyncSession],
    config: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import run_worker

    await seed(queue_db, due=datetime.now(UTC) - timedelta(seconds=1))
    stop = asyncio.Event()
    sender = AsyncMock(spec=PushSender)

    async def send(*args: object) -> PushResult:
        stop.set()
        return PushResult(PushStatus.ACCEPTED, 201)

    def make_sender(settings: Settings) -> PushSender:
        return cast(PushSender, sender)

    sender.send.side_effect = send
    monkeypatch.setattr("app.worker.PushSender", make_sender)
    await asyncio.wait_for(run_worker(queue_db, config, stop), timeout=3)
    sender.send.assert_awaited_once()
    async with queue_db() as session:
        delivery = await session.scalar(select(PushDelivery))
        assert delivery is not None and delivery.status == "accepted"


async def test_worker_logs_safe_diagnostics_and_leaves_claim_for_recovery(
    queue_db: async_sessionmaker[AsyncSession],
    config: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed(queue_db, due=datetime.now(UTC) - timedelta(seconds=1))
    sender = AsyncMock(spec=PushSender)
    sender.send.side_effect = RuntimeError("fake-sensitive-endpoint")
    assert not await run_cycle(queue_db, sender, config)
    assert "RuntimeError" in caplog.text
    assert "fake-sensitive-endpoint" not in caplog.text
    async with queue_db() as session:
        delivery = await session.scalar(select(PushDelivery))
        assert delivery is not None and delivery.status == "processing"
        assert delivery.lease_until is not None
