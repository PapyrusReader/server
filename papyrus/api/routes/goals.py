"""Goal routes backed by owned definitions and real activity."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId
from papyrus.core.database import get_db
from papyrus.schemas.goal import CreateGoalRequest, Goal, GoalList, UpdateGoalRequest
from papyrus.services import goals as service

router = APIRouter()
DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=GoalList, summary="List all goals")
async def list_goals(user_id: CurrentUserId, db: DBSession, is_active: bool | None = None) -> GoalList:
    return GoalList(goals=await service.list_goals(db, user_id, is_active))


@router.post("", response_model=Goal, status_code=status.HTTP_201_CREATED, summary="Create a new goal")
async def create_goal(user_id: CurrentUserId, request: CreateGoalRequest, db: DBSession) -> Goal:
    return await service.create_goal(db, user_id, request)


@router.get("/{goal_id}", response_model=Goal, summary="Get goal details")
async def get_goal(user_id: CurrentUserId, goal_id: UUID, db: DBSession) -> Goal:
    return await service.get_goal(db, user_id, goal_id)


@router.patch("/{goal_id}", response_model=Goal, summary="Update goal")
async def update_goal(user_id: CurrentUserId, goal_id: UUID, request: UpdateGoalRequest, db: DBSession) -> Goal:
    return await service.update_goal(db, user_id, goal_id, request)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete goal")
async def delete_goal(user_id: CurrentUserId, goal_id: UUID, db: DBSession) -> Response:
    await service.delete_goal(db, user_id, goal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
