from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import AuthServiceDep, AuthTokenDep, CurrentUserDep
from app.models.user import User
from app.schemas.auth import AuthLogin, AuthRegister
from app.schemas.user import UserRead
from app.services.auth import (
    InvalidCredentialsError,
    LoginAlreadyExistsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=str, status_code=status.HTTP_201_CREATED)
async def register(data: AuthRegister, auth_service: AuthServiceDep) -> str:
    try:
        return await auth_service.register(data)
    except LoginAlreadyExistsError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Login already exists",
        ) from err


@router.post("/login", response_model=str, status_code=status.HTTP_200_OK)
async def login(data: AuthLogin, auth_service: AuthServiceDep) -> str:
    try:
        return await auth_service.login(data)
    except InvalidCredentialsError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from err


@router.get("/me", response_model=UserRead)
async def get_me(user: CurrentUserDep) -> User:
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    token: AuthTokenDep,
    user: CurrentUserDep,
    auth_service: AuthServiceDep,
) -> Response:
    await auth_service.revoke_session(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
