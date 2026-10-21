from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_subscription import PushSubscription


class PushSubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_endpoint(self, endpoint: str) -> PushSubscription | None:
        stmt = select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_by_user_id(self, user_id: UUID) -> list[PushSubscription]:
        stmt = select(PushSubscription).where(PushSubscription.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_if_unchanged(
        self,
        subscription_id: UUID,
        user_id: UUID,
        endpoint: str,
        p256dh: str,
        auth: str,
        updated_at: datetime,
    ) -> None:
        stmt = (
            delete(PushSubscription)
            .where(
                PushSubscription.id == subscription_id,
                PushSubscription.user_id == user_id,
                PushSubscription.endpoint == endpoint,
                PushSubscription.p256dh == p256dh,
                PushSubscription.auth == auth,
                PushSubscription.updated_at == updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(stmt)

    def add(self, subscription: PushSubscription) -> None:
        self._session.add(subscription)

    async def delete(self, subscription: PushSubscription) -> None:
        await self._session.delete(subscription)

    async def save(self) -> None:
        await self._session.flush()
        await self._session.commit()
