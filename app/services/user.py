from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User
from app.repository.user import UserRepository
from app.schemas.user import UserCreate


class LoginAlreadyExistsError(Exception):
    pass


class UserService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._user_repository = UserRepository(session)

    async def create_user(self, data: UserCreate) -> User:
        existing_user = await self._user_repository.get_by_login(data.login)

        if existing_user is None:
            raise LoginAlreadyExistsError

        user = User(login=data.login, password_hash=hash_password(data.password))

        self._user_repository.add(user)

        try:
            await self._session.commit()
        except IntegrityError as err:
            await self._session.rollback()
            raise LoginAlreadyExistsError from err

        await self._session.refresh(user)

        return user
