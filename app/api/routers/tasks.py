from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import CurrentUserDep, TaskServiceDep
from app.models.task import ReminderTask
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate
from app.services.task import TaskNotFoundError

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate,
    user: CurrentUserDep,
    task_service: TaskServiceDep,
) -> ReminderTask:
    return await task_service.create(user.id, data)


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    user: CurrentUserDep,
    task_service: TaskServiceDep,
) -> list[ReminderTask]:
    return await task_service.list_for_user(user.id)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: UUID,
    user: CurrentUserDep,
    task_service: TaskServiceDep,
) -> ReminderTask:
    try:
        return await task_service.get(user.id, task_id)
    except TaskNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from err


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    user: CurrentUserDep,
    task_service: TaskServiceDep,
) -> ReminderTask:
    try:
        return await task_service.update(user.id, task_id, data)
    except TaskNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from err


@router.post("/{task_id}/complete", response_model=TaskRead)
async def complete_task(
    task_id: UUID,
    user: CurrentUserDep,
    task_service: TaskServiceDep,
) -> ReminderTask:
    try:
        return await task_service.complete(user.id, task_id)
    except TaskNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from err


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    user: CurrentUserDep,
    task_service: TaskServiceDep,
) -> Response:
    try:
        await task_service.delete(user.id, task_id)
    except TaskNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from err
    return Response(status_code=status.HTTP_204_NO_CONTENT)
