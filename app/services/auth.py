from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.models.session import AuthSession
from app.models.user import User
from app.repository.session import SessionRepository
from app.repository.user import UserRepository
from app.schemas.auth import AuthLogin, AuthRegister


class LoginAlreadyExistsError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class SessionNotExist(Exception):
    pass


class AuthService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._auth_repository = UserRepository(session)
        self._session_repository = SessionRepository(session)

    async def register(self, data: AuthRegister) -> str:
        existing_user = await self._auth_repository.get_by_login(data.login)

        if existing_user is not None:
            raise LoginAlreadyExistsError

        hashed_password = await run_in_threadpool(hash_password, data.password)

        user = User(login=data.login, password_hash=hashed_password)

        self._auth_repository.add(user)

        try:
            await self._session.flush()
        except IntegrityError as err:
            await self._session.rollback()
            raise LoginAlreadyExistsError from err

        session_token = self._create_session(user)

        try:
            await self._session.commit()
        except IntegrityError as err:
            await self._session.rollback()
            raise LoginAlreadyExistsError from err
        except Exception:
            await self._session.rollback()
            raise

        return session_token

    async def login(self, data: AuthLogin) -> str:
        existing_user = await self._auth_repository.get_by_login(data.login)

        is_valid_password = await run_in_threadpool(
            verify_password,
            password=data.password,
            password_hash=(
                existing_user.password_hash
                if existing_user is not None
                else DUMMY_PASSWORD_HASH
            ),
        )

        if existing_user is None or not is_valid_password:
            raise InvalidCredentialsError

        session_token = self._create_session(existing_user)

        await self._session.commit()

        return session_token

    async def get_current_user(self, token: str) -> User:
        auth_session = await self._session_repository.get_by_token_hash(
            hash_session_token(token)
        )

        if auth_session is None or auth_session.expires_at <= datetime.now(UTC):
            raise SessionNotExist

        user = await self._auth_repository.get_by_id(auth_session.user_id)

        if user is None:
            raise SessionNotExist

        return user

    async def revoke_session(self, token: str) -> None:
        auth_session = await self._session_repository.get_by_token_hash(
            hash_session_token(token)
        )

        if auth_session is None:
            return

        await self._session_repository.delete(auth_session)
        await self._session.commit()

    def _create_session(self, user: User) -> str:
        token = generate_session_token()
        token_hash = hash_session_token(token)

        expires_at = datetime.now(UTC) + timedelta(days=settings.auth_session_ttl_days)

        auth_session = AuthSession(
            token_hash=token_hash, user_id=user.id, expires_at=expires_at
        )

        self._session_repository.add(auth_session)

        return token
