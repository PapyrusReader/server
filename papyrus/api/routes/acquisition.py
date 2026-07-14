"""Private torrent and indexer acquisition endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId
from papyrus.core.database import get_db
from papyrus.core.security import decrypt_secret_payload, encrypt_secret_payload
from papyrus.models.acquisition import AcquisitionEndpoint as AcquisitionEndpointModel
from papyrus.models.acquisition import AcquisitionJob as AcquisitionJobModel
from papyrus.models.acquisition import AcquisitionRule as AcquisitionRuleModel
from papyrus.schemas.acquisition import (
    AcquisitionCapabilities,
    AcquisitionEndpoint,
    AcquisitionEndpointCreate,
    AcquisitionEndpointUpdate,
    AcquisitionJob,
    AcquisitionRule,
    AcquisitionRuleCreate,
    ArrCommandRequest,
    Release,
    SearchRequest,
    SubmitRequest,
)
from papyrus.services.acquisition import (
    dispatch_arr_command,
    owned_endpoint,
    run_rule,
    search_endpoint,
    submit_to_client,
)

router = APIRouter()
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _credentials(api_key: str | None, username: str | None, password: str | None) -> dict[str, str] | None:
    values = {
        key: value for key, value in {"api_key": api_key, "username": username, "password": password}.items() if value
    }
    if not values:
        return None
    return {"encrypted": encrypt_secret_payload(values)}


def _merge_credentials(endpoint: AcquisitionEndpointModel, replacement: dict[str, str] | None) -> None:
    if replacement is None:
        return
    current = endpoint.credentials or {}
    if encrypted := current.get("encrypted"):
        current = decrypt_secret_payload(encrypted)
    endpoint.credentials = {
        "encrypted": encrypt_secret_payload({**current, **decrypt_secret_payload(replacement["encrypted"])})
    }


@router.get("/capabilities", response_model=AcquisitionCapabilities)
async def acquisition_capabilities() -> AcquisitionCapabilities:
    return AcquisitionCapabilities(
        endpoint_kinds=[
            "qbittorrent",
            "transmission",
            "deluge",
            "prowlarr",
            "torznab",
            "readarr",
            "sonarr",
            "radarr",
            "lidarr",
            "whisparr",
        ],
        indexer_kinds=["prowlarr", "torznab"],
        download_client_kinds=["qbittorrent", "transmission", "deluge"],
        arr_kinds=["readarr", "sonarr", "radarr", "lidarr", "whisparr"],
        arr_commands={
            "readarr": ["AuthorSearch", "BookSearch"],
            "sonarr": ["SeriesSearch", "EpisodeSearch", "MissingEpisodeSearch"],
            "radarr": ["MoviesSearch", "MissingMoviesSearch"],
            "lidarr": ["ArtistSearch", "AlbumSearch", "MissingAlbumSearch"],
            "whisparr": ["SeriesSearch", "EpisodeSearch", "MissingEpisodeSearch"],
        },
    )


@router.get("/endpoints", response_model=list[AcquisitionEndpoint])
async def list_endpoints(user_id: CurrentUserId, db: DbSession) -> list[AcquisitionEndpointModel]:
    result = await db.execute(
        select(AcquisitionEndpointModel)
        .where(AcquisitionEndpointModel.owner_user_id == user_id)
        .order_by(AcquisitionEndpointModel.name)
    )
    return list(result.scalars())


@router.post("/endpoints", response_model=AcquisitionEndpoint, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    user_id: CurrentUserId, request: AcquisitionEndpointCreate, db: DbSession
) -> AcquisitionEndpointModel:
    endpoint = AcquisitionEndpointModel(
        owner_user_id=user_id,
        name=request.name,
        kind=request.kind.value,
        base_url=str(request.base_url),
        credentials=_credentials(
            request.api_key.get_secret_value() if request.api_key else None,
            request.username.get_secret_value() if request.username else None,
            request.password.get_secret_value() if request.password else None,
        ),
        settings=request.settings,
    )
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


@router.patch("/endpoints/{endpoint_id}", response_model=AcquisitionEndpoint)
async def update_endpoint(
    user_id: CurrentUserId, endpoint_id: UUID, request: AcquisitionEndpointUpdate, db: DbSession
) -> AcquisitionEndpointModel:
    endpoint = await owned_endpoint(db, user_id, endpoint_id)
    updates = request.model_dump(exclude_unset=True, exclude={"api_key", "username", "password"})
    for field, value in updates.items():
        setattr(endpoint, field, str(value) if field == "base_url" else value)
    replacement = _credentials(
        request.api_key.get_secret_value() if request.api_key else None,
        request.username.get_secret_value() if request.username else None,
        request.password.get_secret_value() if request.password else None,
    )
    _merge_credentials(endpoint, replacement)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(user_id: CurrentUserId, endpoint_id: UUID, db: DbSession) -> None:
    endpoint = await owned_endpoint(db, user_id, endpoint_id)
    await db.delete(endpoint)
    await db.commit()


@router.post("/search", response_model=list[Release])
async def search_releases(user_id: CurrentUserId, request: SearchRequest, db: DbSession) -> list[Release]:
    statement = select(AcquisitionEndpointModel).where(
        AcquisitionEndpointModel.owner_user_id == user_id,
        AcquisitionEndpointModel.enabled.is_(True),
        AcquisitionEndpointModel.kind.in_(("prowlarr", "torznab")),
    )
    if request.endpoint_ids:
        statement = statement.where(AcquisitionEndpointModel.endpoint_id.in_(request.endpoint_ids))
    result = await db.execute(statement)
    releases: list[Release] = []
    for endpoint in result.scalars():
        releases.extend(await search_endpoint(endpoint, request.query))
    return releases


@router.post("/submissions", response_model=AcquisitionJob, status_code=status.HTTP_201_CREATED)
async def submit_release(user_id: CurrentUserId, request: SubmitRequest, db: DbSession) -> AcquisitionJobModel:
    endpoint = await owned_endpoint(db, user_id, request.endpoint_id)
    job = AcquisitionJobModel(
        owner_user_id=user_id, endpoint_id=endpoint.endpoint_id, title=request.title, download_url=request.download_url
    )
    db.add(job)
    try:
        job.client_reference = await submit_to_client(
            endpoint, request.download_url, request.category, request.save_path
        )
        job.status = "submitted"
    except HTTPException as exc:
        job.status = "failed"
        job.error = str(exc.detail)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/arr/{endpoint_id}/commands", response_model=AcquisitionJob, status_code=status.HTTP_201_CREATED)
async def run_arr_command(
    user_id: CurrentUserId, endpoint_id: UUID, request: ArrCommandRequest, db: DbSession
) -> AcquisitionJobModel:
    """Delegate a managed search/acquisition to Readarr or another Arr app."""
    endpoint = await owned_endpoint(db, user_id, endpoint_id)
    job = AcquisitionJobModel(
        owner_user_id=user_id,
        endpoint_id=endpoint.endpoint_id,
        title=request.command,
        download_url=f"arr-command:{request.command}",
    )
    db.add(job)
    try:
        job.client_reference = await dispatch_arr_command(endpoint, request.command, request.ids)
        job.status = "submitted"
    except HTTPException as exc:
        job.status = "failed"
        job.error = str(exc.detail)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/jobs", response_model=list[AcquisitionJob])
async def list_jobs(user_id: CurrentUserId, db: DbSession) -> list[AcquisitionJobModel]:
    result = await db.execute(
        select(AcquisitionJobModel)
        .where(AcquisitionJobModel.owner_user_id == user_id)
        .order_by(AcquisitionJobModel.created_at.desc())
    )
    return list(result.scalars())


@router.get("/rules", response_model=list[AcquisitionRule])
async def list_rules(user_id: CurrentUserId, db: DbSession) -> list[AcquisitionRuleModel]:
    result = await db.execute(select(AcquisitionRuleModel).where(AcquisitionRuleModel.owner_user_id == user_id))
    return list(result.scalars())


@router.post("/rules", response_model=AcquisitionRule, status_code=status.HTTP_201_CREATED)
async def create_rule(user_id: CurrentUserId, request: AcquisitionRuleCreate, db: DbSession) -> AcquisitionRuleModel:
    await owned_endpoint(db, user_id, request.download_client_id)
    rule = AcquisitionRuleModel(
        owner_user_id=user_id,
        name=request.name,
        query=request.query,
        endpoint_ids=[str(value) for value in request.endpoint_ids] if request.endpoint_ids else None,
        download_client_id=request.download_client_id,
        filters=request.filters,
        enabled=request.enabled,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.post("/rules/{rule_id}/run", response_model=list[AcquisitionJob])
async def run_acquisition_rule(user_id: CurrentUserId, rule_id: UUID, db: DbSession) -> list[AcquisitionJobModel]:
    result = await db.execute(
        select(AcquisitionRuleModel).where(
            AcquisitionRuleModel.rule_id == rule_id, AcquisitionRuleModel.owner_user_id == user_id
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Acquisition rule not found")
    return await run_rule(db, rule)
