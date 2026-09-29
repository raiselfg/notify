from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserRead
from app.services.user import (
    InvalidCredentialsError,
    LoginAlreadyExistsError,
    UserService,
)

router = APIRouter(
    prefix="/users",
    tags=["users"],
)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreate,
    session: Annotated[
        AsyncSession,
        Depends(get_db),
    ],
) -> User:
    user_service = UserService(session)

    try:
        return await user_service.create_user(data)
    except LoginAlreadyExistsError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Login already exists",
        ) from err


@router.post("/login", response_model=UserRead, status_code=status.HTTP_200_OK)
async def login(
    data: UserLogin, session: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    user_service = UserService(session)

    try:
        return await user_service.login(data)
    except InvalidCredentialsError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from err
