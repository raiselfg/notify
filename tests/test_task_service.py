from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repository.task import TaskRepository
from app.schemas.task import TaskCreate
from app.services.task import TaskService


async def test_create_schedules_first_reminder_after_interval() -> None:
    session = AsyncMock(spec=AsyncSession)
    service = TaskService(session)
    repository = MagicMock(spec=TaskRepository)
    service._repository = repository
    user_id = uuid4()
    before_create = datetime.now(UTC)

    task = await service.create(
        user_id,
        TaskCreate(title="  Take a break  ", interval_minutes=30),
    )

    after_create = datetime.now(UTC)
    assert task.user_id == user_id
    assert task.title == "Take a break"
    assert task.interval_minutes == 30
    assert before_create + timedelta(minutes=30) <= task.next_reminder_at
    assert task.next_reminder_at <= after_create + timedelta(minutes=30)
    repository.add.assert_called_once_with(task)
    session.commit.assert_awaited_once()


def test_create_rejects_blank_title_and_non_positive_interval() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(title="   ", interval_minutes=30)

    with pytest.raises(ValidationError):
        TaskCreate(title="Take a break", interval_minutes=0)


@pytest.mark.parametrize("operation", ["update", "complete"])
async def test_changed_task_can_be_serialized_after_commit(operation: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.models.task import ReminderTask
    from app.schemas.task import TaskRead, TaskUpdate

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        task = ReminderTask(
            user_id=uuid4(),
            title="Before",
            interval_minutes=30,
            next_reminder_at=datetime.now(UTC),
        )
        db.add(task)
        db.commit()
        session = AsyncMock(spec=AsyncSession)
        session.flush.side_effect = db.flush
        session.commit.side_effect = db.commit
        session.refresh.side_effect = db.refresh
        service = TaskService(session)
        repository = MagicMock(spec=TaskRepository)
        repository.get_one_by_user_id = AsyncMock(return_value=task)
        service._repository = repository

        if operation == "update":
            result = await service.update(
                task.user_id, task.id, TaskUpdate(title="After")
            )
            assert result.title == "After"
        else:
            result = await service.complete(task.user_id, task.id)
            assert result.completed_at is not None

        db.close()
        assert TaskRead.model_validate(result).updated_at is not None
    engine.dispose()
