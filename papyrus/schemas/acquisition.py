"""HTTP schemas for private acquisition integrations."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr


class EndpointKind(StrEnum):
    QBITTORRENT = "qbittorrent"
    TRANSMISSION = "transmission"
    DELUGE = "deluge"
    PROWLARR = "prowlarr"
    TORZNAB = "torznab"
    NEWZNAB = "newznab"
    READARR = "readarr"
    SONARR = "sonarr"
    RADARR = "radarr"
    LIDARR = "lidarr"
    WHISPARR = "whisparr"


class AcquisitionEndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: EndpointKind
    base_url: HttpUrl
    api_key: SecretStr | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None
    settings: dict[str, object] | None = None


class AcquisitionEndpointUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    api_key: SecretStr | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None
    settings: dict[str, object] | None = None
    enabled: bool | None = None


class AcquisitionEndpoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    endpoint_id: UUID
    name: str
    kind: EndpointKind
    base_url: str
    enabled: bool
    settings: dict[str, object] | None = None
    created_at: datetime | None = None


class Release(BaseModel):
    title: str
    download_url: str
    protocol: str
    indexer: str
    size_bytes: int | None = None
    seeders: int | None = None
    publish_date: datetime | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    endpoint_ids: list[UUID] | None = None


class SubmitRequest(BaseModel):
    endpoint_id: UUID
    title: str = Field(min_length=1, max_length=500)
    download_url: str = Field(min_length=1, max_length=4096)
    category: str | None = Field(None, max_length=100)
    save_path: str | None = Field(None, max_length=1024)


class ArrCommandRequest(BaseModel):
    """A Servarr command and the IDs scoped to that application's library."""

    command: str = Field(min_length=1, max_length=80)
    ids: list[int] = Field(default_factory=list, max_length=100)


class AcquisitionRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=500)
    endpoint_ids: list[UUID] | None = None
    download_client_id: UUID
    filters: dict[str, object] | None = None
    enabled: bool = True


class AcquisitionRule(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    rule_id: UUID
    name: str
    query: str
    endpoint_ids: list[UUID] | None = None
    download_client_id: UUID | None
    filters: dict[str, object] | None = None
    enabled: bool
    last_run_at: datetime | None = None


class AcquisitionJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    job_id: UUID
    endpoint_id: UUID
    rule_id: UUID | None
    title: str
    download_url: str
    status: str
    client_reference: str | None = None
    error: str | None = None
    created_at: datetime | None = None
