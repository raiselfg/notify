from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.services.auth import AuthService, SessionNotExist

bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthService:
    return AuthService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_auth_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials


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
            headers={"WWW-Authenticate": "Bearer"},
        ) from err


CurrentUserDep = Annotated[User, Depends(get_current_user)]
