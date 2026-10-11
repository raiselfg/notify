from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ReminderTask


class TaskRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def add(self, task: ReminderTask) -> None:
        self._session.add(task)

    async def get_one_by_user_id(
        self,
        task_id: UUID,
        user_id: UUID,
    ) -> ReminderTask | None:
        stmt = select(ReminderTask).where(
            ReminderTask.id == task_id,
            ReminderTask.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_by_user_id(self, user_id: UUID) -> list[ReminderTask]:
        stmt = (
            select(ReminderTask)
            .where(ReminderTask.user_id == user_id)
            .order_by(ReminderTask.created_at.desc(), ReminderTask.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, task: ReminderTask) -> None:
        await self._session.delete(task)
