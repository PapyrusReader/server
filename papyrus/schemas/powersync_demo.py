"""Schemas for the PowerSync sandbox demo."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PowerSyncDemoItem(BaseModel):
    """Demo item returned from the source database."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    owner_user_id: UUID
    title: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class PowerSyncDemoItemList(BaseModel):
    """Collection of demo items."""

    items: list[PowerSyncDemoItem]


class PowerSyncUploadMutation(BaseModel):
    """Single mutation uploaded from the PowerSync client queue."""

    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(alias="type")
    op: Literal["PUT", "PATCH", "DELETE", "put", "patch", "delete"]
    id: str
    op_id: int | None = Field(default=None, alias="op_id")
    tx_id: int | None = None
    op_data: dict[str, Any] | None = Field(default=None, alias="data")


class PowerSyncUploadRequest(BaseModel):
    """Mutation batch uploaded from the PowerSync client queue."""

    batch: list[PowerSyncUploadMutation]


class PowerSyncUploadResponse(BaseModel):
    """Summary of an applied mutation batch."""

    applied_count: int


class PowerSyncSandboxConfigResponse(BaseModel):
    """Runtime configuration for the debug PowerSync sandbox."""

    register_url: str
    login_url: str
    refresh_url: str
    logout_url: str
    google_start_url: str
    exchange_url: str
    me_url: str
    powersync_token_url: str
    powersync_endpoint: str
    items_url: str
    upload_url: str
    redirect_uri: str
    sdk_module_url: str
    db_worker_url: str
    sync_worker_url: str
    db_filename: str
