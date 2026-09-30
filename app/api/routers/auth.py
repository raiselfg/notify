from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import AuthServiceDep
from app.models.user import User
from app.schemas.auth import AuthLogin, AuthRegister
from app.schemas.user import UserRead
from app.services.auth import (
    InvalidCredentialsError,
    LoginAlreadyExistsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: AuthRegister, auth_service: AuthServiceDep) -> User:
    try:
        return await auth_service.register(data)
    except LoginAlreadyExistsError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Login already exists",
        ) from err


@router.post("/login", response_model=UserRead, status_code=status.HTTP_200_OK)
async def login(data: AuthLogin, auth_service: AuthServiceDep) -> User:
    try:
        return await auth_service.login(data)
    except InvalidCredentialsError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from err
