from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_subscription import PushSubscription


class PushSubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_endpoint(self, endpoint: str) -> PushSubscription | None:
        stmt = select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    def add(self, subscription: PushSubscription) -> None:
        self._session.add(subscription)

    async def delete(self, subscription: PushSubscription) -> None:
        await self._session.delete(subscription)

    async def save(self) -> None:
        await self._session.flush()
        await self._session.commit()
