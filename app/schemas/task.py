from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    interval_minutes: int = Field(ge=1, le=525_600)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    interval_minutes: int | None = Field(default=None, ge=1, le=525_600)

    @model_validator(mode="after")
    def require_update(self) -> "TaskUpdate":
        if self.title is None and self.interval_minutes is None:
            raise ValueError("At least one field must be provided")
        return self


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    interval_minutes: int
    next_reminder_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
