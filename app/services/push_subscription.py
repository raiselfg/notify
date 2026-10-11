from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_subscription import PushSubscription
from app.repository.push_subscription import PushSubscriptionRepository
from app.schemas.push_subscription import PushSubscriptionInput


class PushSubscriptionConflictError(Exception):
    pass


class PushSubscriptionService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repository = PushSubscriptionRepository(session)

    async def save(self, user_id: UUID, data: PushSubscriptionInput) -> None:
        subscription = await self._repository.get_by_endpoint(data.endpoint)

        if subscription is not None and subscription.user_id != user_id:
            raise PushSubscriptionConflictError

        if subscription is None:
            subscription = PushSubscription(
                user_id=user_id,
                endpoint=data.endpoint,
                p256dh=data.keys.p256dh,
                auth=data.keys.auth,
            )
            self._repository.add(subscription)
        else:
            subscription.p256dh = data.keys.p256dh
            subscription.auth = data.keys.auth

        try:
            await self._repository.save()
        except IntegrityError as err:
            await self._session.rollback()
            raise PushSubscriptionConflictError from err
        except Exception:
            await self._session.rollback()
            raise

    async def delete(self, user_id: UUID, endpoint: str) -> None:
        subscription = await self._repository.get_by_endpoint(endpoint)
        if subscription is None or subscription.user_id != user_id:
            return

        try:
            await self._repository.delete(subscription)
            await self._repository.save()
        except Exception:
            await self._session.rollback()
            raise
