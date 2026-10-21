from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from random import uniform
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.push_delivery import PushDelivery, PushEvent
from app.models.push_subscription import PushSubscription
from app.repository.push_queue import PushQueueRepository
from app.repository.push_subscription import PushSubscriptionRepository
from app.schemas.push import PushMessage
from app.schemas.push_subscription import PushSubscriptionInput, PushSubscriptionKeys
from app.services.push import PushResult, PushStatus
from app.services.push_delivery import SubscriptionSnapshot


@dataclass(frozen=True)
class DeliveryClaim:
    id: UUID
    token: UUID


@dataclass(frozen=True)
class PreparedDelivery:
    claim: DeliveryClaim
    subscription: PushSubscriptionInput = field(repr=False)
    snapshot: SubscriptionSnapshot = field(repr=False)
    message: PushMessage
    expires_at: datetime


def finish_delivery(
    delivery: PushDelivery, status: str, reason: str | None = None
) -> None:
    delivery.status = status
    delivery.last_reason = reason
    delivery.lease_token = None
    delivery.lease_until = None


def owns_claim(delivery: PushDelivery, claim: DeliveryClaim, now: datetime) -> bool:
    return (
        delivery.status == "processing"
        and delivery.lease_token == claim.token
        and delivery.lease_until is not None
        and delivery.lease_until > now
    )


class PushQueueService:
    """Each method owns a short transaction on a dedicated session."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = PushQueueRepository(session)
        self._subscriptions = PushSubscriptionRepository(session)
        self._lease_duration = timedelta(seconds=settings.push_timeout_seconds + 30)

    async def enqueue_due(self, now: datetime, limit: int = 100) -> int:
        async with self._session.begin():
            tasks = await self._repository.get_due_tasks(now, limit)
            for task in tasks:
                due = task.next_reminder_at
                interval = timedelta(minutes=task.interval_minutes)
                next_time = due + ((now - due) // interval + 1) * interval
                subscriptions = await self._subscriptions.get_all_by_user_id(
                    task.user_id
                )
                event = PushEvent(
                    id=uuid4(),
                    task_id=task.id,
                    schedule_version=task.schedule_version,
                    scheduled_at=due,
                    created_at=now,
                    expires_at=min(
                        next_time,
                        now + timedelta(seconds=self._settings.push_ttl_seconds),
                    ),
                    title=task.title,
                    no_subscriptions=not subscriptions,
                )
                self._session.add(event)
                await self._session.flush()
                for subscription in subscriptions:
                    self._session.add(
                        PushDelivery(
                            event_id=event.id,
                            subscription_id=subscription.id,
                            available_at=now,
                        )
                    )
                task.next_reminder_at = next_time
            return len(tasks)

    async def claim_ready(self, now: datetime) -> list[DeliveryClaim]:
        claims: list[DeliveryClaim] = []
        async with self._session.begin():
            ready = await self._repository.get_ready_deliveries(
                now, self._settings.push_max_concurrency
            )
            for delivery, event in ready:
                if event.expires_at <= now:
                    finish_delivery(delivery, "expired")
                elif delivery.subscription_id is None:
                    finish_delivery(delivery, "cancelled", "subscription_removed")
                elif delivery.attempt_count >= self._settings.push_max_attempts:
                    finish_delivery(delivery, "failed", "attempt_limit")
                else:
                    delivery.status = "processing"
                    delivery.attempt_count += 1
                    token = uuid4()
                    delivery.lease_token = token
                    delivery.lease_until = now + self._lease_duration
                    claims.append(DeliveryClaim(delivery.id, token))
        return claims

    async def prepare(
        self, claim: DeliveryClaim, now: datetime | None = None
    ) -> PreparedDelivery | None:
        async with self._session.begin():
            reference = await self._session.get(PushDelivery, claim.id)
            if reference is None:
                return None
            event = await self._session.get(PushEvent, reference.event_id)
            if event is None:
                return None
            # Task mutations and the scheduler lock this row first as well.
            task = await self._repository.get_task(event.task_id)
            delivery = await self._repository.get_delivery(claim.id)
            now = now or datetime.now(UTC)
            if delivery is None or not owns_claim(delivery, claim, now):
                return None
            if task is None or task.completed_at is not None:
                finish_delivery(delivery, "cancelled", "task_inactive")
                return None
            if task.schedule_version != event.schedule_version:
                finish_delivery(delivery, "cancelled", "schedule_changed")
                return None
            if event.expires_at <= now:
                finish_delivery(delivery, "expired")
                return None
            subscription = (
                await self._session.get(PushSubscription, delivery.subscription_id)
                if delivery.subscription_id is not None
                else None
            )
            if subscription is None or subscription.user_id != task.user_id:
                finish_delivery(delivery, "cancelled", "subscription_removed")
                return None
            try:
                data = PushSubscriptionInput(
                    endpoint=subscription.endpoint,
                    keys=PushSubscriptionKeys(
                        p256dh=subscription.p256dh, auth=subscription.auth
                    ),
                )
            except ValidationError:
                finish_delivery(delivery, "failed", "invalid_subscription_data")
                return None
            delivery.lease_until = now + self._lease_duration
            return PreparedDelivery(
                claim,
                data,
                SubscriptionSnapshot(
                    subscription.id,
                    subscription.user_id,
                    subscription.endpoint,
                    subscription.p256dh,
                    subscription.auth,
                    subscription.updated_at,
                ),
                PushMessage(
                    event_id=event.id,
                    task_id=event.task_id,
                    title=event.title,
                    body="Напоминание о задаче",
                ),
                event.expires_at,
            )

    async def record_result(
        self,
        prepared: PreparedDelivery,
        result: PushResult,
        now: datetime | None = None,
    ) -> bool:
        async with self._session.begin():
            if result.status == PushStatus.INVALID_SUBSCRIPTION:
                await self._session.get(
                    PushSubscription, prepared.snapshot.id, with_for_update=True
                )
            delivery = await self._repository.get_delivery(prepared.claim.id)
            now = now or datetime.now(UTC)
            if delivery is None or not owns_claim(delivery, prepared.claim, now):
                return False
            delivery.last_status_code = result.status_code
            reason = (
                result.reason
                if result.reason
                in {"unsafe_endpoint", "timeout", "network_error", "invalid_push_data"}
                else None
            )
            if result.status == PushStatus.RETRY:
                if prepared.expires_at <= now:
                    finish_delivery(delivery, "expired")
                elif delivery.attempt_count >= self._settings.push_max_attempts:
                    finish_delivery(delivery, "failed", "attempt_limit")
                else:
                    base = 30 * 2 ** (delivery.attempt_count - 1)
                    delay = max(
                        base + uniform(0, base * 0.1), result.retry_after_seconds or 0
                    )
                    if delay >= (prepared.expires_at - now).total_seconds():
                        finish_delivery(delivery, "expired")
                    else:
                        finish_delivery(delivery, "pending", reason)
                        delivery.available_at = now + timedelta(seconds=delay)
            else:
                finish_delivery(delivery, result.status.value, reason)
            if result.status == PushStatus.INVALID_SUBSCRIPTION:
                snapshot = prepared.snapshot
                await self._subscriptions.delete_if_unchanged(
                    snapshot.id,
                    snapshot.user_id,
                    snapshot.endpoint,
                    snapshot.p256dh,
                    snapshot.auth,
                    snapshot.updated_at,
                )
            return True
