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
