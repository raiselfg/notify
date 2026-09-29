from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repository.user import UserRepository
from app.schemas.user import UserCreate, UserLogin


class LoginAlreadyExistsError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class UserService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._user_repository = UserRepository(session)

    async def create_user(self, data: UserCreate) -> User:
        existing_user = await self._user_repository.get_by_login(data.login)

        if existing_user is not None:
            raise LoginAlreadyExistsError

        hashed_password = await run_in_threadpool(hash_password, data.password)

        user = User(login=data.login, password_hash=hashed_password)

        self._user_repository.add(user)

        try:
            await self._session.commit()
        except IntegrityError as err:
            await self._session.rollback()
            raise LoginAlreadyExistsError from err

        await self._session.refresh(user)

        return user

    async def login(self, data: UserLogin) -> User:
        existing_user = await self._user_repository.get_by_login(data.login)

        if existing_user is None:
            raise InvalidCredentialsError

        is_valid_password = await run_in_threadpool(
            verify_password,
            password=data.password,
            password_hash=existing_user.password_hash,
        )

        if not is_valid_password:
            raise InvalidCredentialsError

        return existing_user
