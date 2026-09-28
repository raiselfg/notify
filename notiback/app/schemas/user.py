from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    login: str = Field(
        min_length=3,
        max_length=32,
    )
    password: str = Field(
        min_length=8,
        max_length=128,
    )


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    login: str
    created_at: datetime
