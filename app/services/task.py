from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ReminderTask
from app.repository.task import TaskRepository
from app.schemas.task import TaskCreate, TaskUpdate


class TaskNotFoundError(Exception):
    pass


class TaskService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repository = TaskRepository(session)

    async def create(self, user_id: UUID, data: TaskCreate) -> ReminderTask:
        now = datetime.now(UTC)
        task = ReminderTask(
            user_id=user_id,
            title=data.title,
            interval_minutes=data.interval_minutes,
            next_reminder_at=now + timedelta(minutes=data.interval_minutes),
        )
        self._repository.add(task)
        await self._save()
        return task

    async def list_for_user(self, user_id: UUID) -> list[ReminderTask]:
        return await self._repository.get_all_by_user_id(user_id)

    async def get(self, user_id: UUID, task_id: UUID) -> ReminderTask:
        task = await self._repository.get_one_by_user_id(task_id, user_id)
        if task is None:
            raise TaskNotFoundError
        return task

    async def update(
        self,
        user_id: UUID,
        task_id: UUID,
        data: TaskUpdate,
    ) -> ReminderTask:
        task = await self.get(user_id, task_id)
        updates = data.model_dump(exclude_unset=True, exclude_none=True)

        if "title" in updates:
            task.title = updates["title"]

        if "interval_minutes" in updates:
            task.interval_minutes = updates["interval_minutes"]
            if task.completed_at is None:
                task.next_reminder_at = datetime.now(UTC) + timedelta(
                    minutes=task.interval_minutes
                )

        await self._save()
        await self._session.refresh(task)
        return task

    async def complete(self, user_id: UUID, task_id: UUID) -> ReminderTask:
        task = await self.get(user_id, task_id)
        if task.completed_at is None:
            task.completed_at = datetime.now(UTC)
            await self._save()
            await self._session.refresh(task)
        return task

    async def delete(self, user_id: UUID, task_id: UUID) -> None:
        task = await self.get(user_id, task_id)
        await self._repository.delete(task)
        await self._save()

    async def _save(self) -> None:
        try:
            await self._session.flush()
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
