from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


class PushMessage(BaseModel):
    version: Literal[1] = 1
    event_id: UUID
    task_id: UUID
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=500)

    @computed_field
    @property
    def url(self) -> str:
        return f"/tasks/{self.task_id}"

    @computed_field
    @property
    def tag(self) -> str:
        return f"task-{self.task_id}"
