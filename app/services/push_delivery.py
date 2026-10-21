import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repository.push_subscription import PushSubscriptionRepository
from app.schemas.push import PushMessage
from app.schemas.push_subscription import PushSubscriptionInput, PushSubscriptionKeys
from app.services.push import PushResult, PushSender, PushStatus


@dataclass(frozen=True)
class SubscriptionSnapshot:
    id: UUID
    user_id: UUID
    endpoint: str = field(repr=False)
    p256dh: str = field(repr=False)
    auth: str = field(repr=False)
    updated_at: datetime


class PushDeliveryService:
    """Perform one attempt per subscription using a dedicated DB session."""

    def __init__(self, session: AsyncSession, sender: PushSender) -> None:
        self._session = session
        self._repository = PushSubscriptionRepository(session)
        self._sender = sender

    async def _send(
        self, target: SubscriptionSnapshot, message: PushMessage, expires_at: datetime
    ) -> PushResult:
        try:
            subscription = PushSubscriptionInput(
                endpoint=target.endpoint,
                keys=PushSubscriptionKeys(p256dh=target.p256dh, auth=target.auth),
            )
        except ValidationError:
            return PushResult(PushStatus.FAILED, reason="invalid_subscription_data")
        return await self._sender.send(subscription, message, expires_at)

    async def send_for_user(
        self, user_id: UUID, message: PushMessage, expires_at: datetime
    ) -> dict[UUID, PushResult]:
        try:
            subscriptions = await self._repository.get_all_by_user_id(user_id)
            targets = [
                SubscriptionSnapshot(
                    subscription.id,
                    subscription.user_id,
                    subscription.endpoint,
                    subscription.p256dh,
                    subscription.auth,
                    subscription.updated_at,
                )
                for subscription in subscriptions
            ]
            # End the read transaction before making any network requests.
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

        results = await asyncio.gather(
            *(self._send(target, message, expires_at) for target in targets)
        )
        invalid = [
            target
            for target, result in zip(targets, results, strict=True)
            if result.status == PushStatus.INVALID_SUBSCRIPTION
        ]
        if invalid:
            try:
                for target in invalid:
                    await self._repository.delete_if_unchanged(
                        target.id,
                        target.user_id,
                        target.endpoint,
                        target.p256dh,
                        target.auth,
                        target.updated_at,
                    )
                await self._repository.save()
            except Exception:
                await self._session.rollback()
                raise
        return {
            target.id: result for target, result in zip(targets, results, strict=True)
        }
