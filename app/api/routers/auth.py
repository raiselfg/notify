from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import (
    SESSION_COOKIE_NAME,
    AuthServiceDep,
    AuthTokenDep,
    CurrentUserDep,
)
from app.core.config import settings
from app.models.user import User
from app.schemas.auth import AuthLogin, AuthRegister
from app.schemas.user import UserRead
from app.services.auth import (
    InvalidCredentialsError,
    LoginAlreadyExistsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.auth_session_ttl_days * 24 * 60 * 60,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(data: AuthRegister, auth_service: AuthServiceDep) -> Response:
    try:
        token = await auth_service.register(data)
    except LoginAlreadyExistsError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Login already exists",
        ) from err

    response = Response(status_code=status.HTTP_201_CREATED)
    set_session_cookie(response, token)
    return response


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(data: AuthLogin, auth_service: AuthServiceDep) -> Response:
    try:
        token = await auth_service.login(data)
    except InvalidCredentialsError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from err

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_session_cookie(response, token)
    return response


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
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response
