from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import AuthSession


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def add(self, auth_session: AuthSession) -> None:
        self._session.add(auth_session)

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        stmt = select(AuthSession).where(AuthSession.token_hash == token_hash)
        res = await self._session.execute(stmt)

        return res.scalar_one_or_none()

    async def delete(self, session: AuthSession) -> None:
        await self._session.delete(session)
