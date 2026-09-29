from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def add(self, user: User) -> None:
        self._session.add(user)

    async def get_by_login(self, login: str) -> User | None:
        stmt = select(User).where(User.login == login)

        res = await self._session.execute(stmt)

        return res.scalar_one_or_none()

    async def get_by_id(self, id: str) -> User | None:
        stmt = select(User).where(User.id == id)

        res = await self._session.execute(stmt)

        return res.scalar_one_or_none()
