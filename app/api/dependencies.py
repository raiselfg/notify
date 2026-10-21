from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.services.auth import AuthService, SessionNotExist
from app.services.task import TaskService

SESSION_COOKIE_NAME = "__Host-session"
cookie_scheme = APIKeyCookie(name=SESSION_COOKIE_NAME, auto_error=False)


async def verify_request_origin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    origin = request.headers.get("origin")
    if origin != expected_origin and origin not in settings.cors_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid request origin",
        )


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthService:
    return AuthService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_task_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TaskService:
    return TaskService(session)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


async def get_auth_token(
    token: Annotated[
        str | None,
        Depends(cookie_scheme),
    ],
) -> str:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    return token


AuthTokenDep = Annotated[str, Depends(get_auth_token)]


async def get_current_user(
    token: AuthTokenDep,
    auth_service: AuthServiceDep,
) -> User:
    try:
        return await auth_service.get_current_user(token)
    except SessionNotExist as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        ) from err


CurrentUserDep = Annotated[User, Depends(get_current_user)]
