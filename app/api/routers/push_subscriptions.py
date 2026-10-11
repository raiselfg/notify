from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUserDep
from app.db.session import get_db
from app.schemas.push_subscription import (
    PushSubscriptionEndpoint,
    PushSubscriptionInput,
)
from app.services.push_subscription import (
    PushSubscriptionConflictError,
    PushSubscriptionService,
)

router = APIRouter(prefix="/push/subscriptions", tags=["push"])


def get_push_subscription_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PushSubscriptionService:
    return PushSubscriptionService(session)


PushSubscriptionServiceDep = Annotated[
    PushSubscriptionService,
    Depends(get_push_subscription_service),
]


@router.put("", status_code=status.HTTP_204_NO_CONTENT)
async def save_subscription(
    data: PushSubscriptionInput,
    user: CurrentUserDep,
    service: PushSubscriptionServiceDep,
) -> Response:
    try:
        await service.save(user.id, data)
    except PushSubscriptionConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subscription is already registered",
        ) from err
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    data: PushSubscriptionEndpoint,
    user: CurrentUserDep,
    service: PushSubscriptionServiceDep,
) -> Response:
    await service.delete(user.id, data.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
