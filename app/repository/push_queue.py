from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_delivery import PushDelivery, PushEvent
from app.models.task import ReminderTask


class PushQueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_task(self, task_id: UUID) -> ReminderTask | None:
        stmt = (
            select(ReminderTask)
            .where(ReminderTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_due_tasks(self, now: datetime, limit: int) -> list[ReminderTask]:
        stmt = (
            select(ReminderTask)
            .where(
                ReminderTask.completed_at.is_(None),
                ReminderTask.next_reminder_at <= now,
            )
            .order_by(ReminderTask.next_reminder_at, ReminderTask.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_ready_deliveries(
        self, now: datetime, limit: int
    ) -> list[tuple[PushDelivery, PushEvent]]:
        stmt = (
            select(PushDelivery, PushEvent)
            .join(PushEvent, PushEvent.id == PushDelivery.event_id)
            .where(
                or_(
                    and_(
                        PushDelivery.status == "pending",
                        PushDelivery.available_at <= now,
                    ),
                    and_(
                        PushDelivery.status == "processing",
                        PushDelivery.lease_until <= now,
                    ),
                )
            )
            .order_by(PushDelivery.available_at, PushDelivery.id)
            .limit(limit)
            .with_for_update(of=PushDelivery, skip_locked=True)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_delivery(self, delivery_id: UUID) -> PushDelivery | None:
        stmt = (
            select(PushDelivery)
            .where(PushDelivery.id == delivery_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
