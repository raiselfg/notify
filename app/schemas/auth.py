from pydantic import BaseModel, Field


class AuthRegister(BaseModel):
    login: str = Field(
        min_length=3,
        max_length=32,
    )
    password: str = Field(
        min_length=8,
        max_length=128,
    )


class AuthLogin(BaseModel):
    login: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)
