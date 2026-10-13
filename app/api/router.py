from fastapi import APIRouter, Depends

from app.api.dependencies import verify_request_origin
from app.api.routers.auth import router as auth_router
from app.api.routers.push_subscriptions import router as push_subscriptions_router
from app.api.routers.tasks import router as tasks_router

api_router = APIRouter(prefix="/api", dependencies=[Depends(verify_request_origin)])

api_router.include_router(auth_router)
api_router.include_router(tasks_router)
api_router.include_router(push_subscriptions_router)
