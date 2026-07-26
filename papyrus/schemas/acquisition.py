"""HTTP schemas for private acquisition integrations."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator, model_validator


class EndpointKind(StrEnum):
    QBITTORRENT = "qbittorrent"
    TRANSMISSION = "transmission"
    DELUGE = "deluge"
    PROWLARR = "prowlarr"
    TORZNAB = "torznab"
    READARR = "readarr"
    SONARR = "sonarr"
    RADARR = "radarr"
    LIDARR = "lidarr"
    WHISPARR = "whisparr"


class AcquisitionCapabilities(BaseModel):
    enabled: bool = True
    managed_downloads_ready: bool = False
    endpoint_kinds: list[EndpointKind]
    indexer_kinds: list[EndpointKind]
    download_client_kinds: list[EndpointKind]
    arr_kinds: list[EndpointKind]
    arr_commands: dict[EndpointKind, list[str]]


class AcquisitionEndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: EndpointKind
    base_url: HttpUrl
    api_key: SecretStr | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None
    download_root: str | None = Field(None, min_length=1, max_length=2048)
    settings: dict[str, object] | None = None

    @field_validator("base_url")
    @classmethod
    def validate_endpoint_url(cls, value: HttpUrl) -> HttpUrl:
        return validate_endpoint_url(value)


class AcquisitionEndpointUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    api_key: SecretStr | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None
    download_root: str | None = Field(None, min_length=1, max_length=2048)
    settings: dict[str, object] | None = None
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def validate_endpoint_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
        return validate_endpoint_url(value)


class AcquisitionEndpointTest(BaseModel):
    endpoint_id: UUID | None = None
    kind: EndpointKind | None = None
    base_url: HttpUrl | None = None
    api_key: SecretStr | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None

    @field_validator("base_url")
    @classmethod
    def validate_endpoint_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
        return validate_endpoint_url(value)

    @model_validator(mode="after")
    def validate_endpoint_target(self) -> Self:
        if self.endpoint_id is None and (self.kind is None or self.base_url is None):
            raise ValueError("kind and base_url are required for an unsaved endpoint")
        return self


class AcquisitionEndpointTestResult(BaseModel):
    ok: bool


class AcquisitionEndpoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    endpoint_id: UUID
    name: str
    kind: EndpointKind
    base_url: str
    download_root: str | None = None
    enabled: bool
    settings: dict[str, object] | None = None
    created_at: datetime | None = None


class Release(BaseModel):
    title: str
    release_token: str
    protocol: str
    indexer: str
    size_bytes: int | None = None
    seeders: int | None = None
    publish_date: datetime | None = None
    format_hints: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    endpoint_ids: list[UUID] | None = None


class SubmitRequest(BaseModel):
    endpoint_id: UUID
    release_token: str = Field(min_length=1)


class BatchSubmitRequest(BaseModel):
    endpoint_id: UUID
    release_tokens: list[str] = Field(min_length=1, max_length=100)


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
    endpoint_id: UUID | None
    rule_id: UUID | None
    book_id: UUID | None
    title: str
    status: str
    client_reference: str | None = None
    client_hash: str | None = None
    client_state: str | None = None
    progress_basis_points: int | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    download_speed_bytes_per_second: int | None = None
    eta_seconds: int | None = None
    selected_file_path: str | None = None
    retry_count: int = 0
    error: str | None = None
    next_poll_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


class BatchSubmissionItem(BaseModel):
    index: int
    job: AcquisitionJob | None = None
    error: str | None = None


class BatchSubmissionResponse(BaseModel):
    items: list[BatchSubmissionItem]


class AcquisitionJobPage(BaseModel):
    items: list[AcquisitionJob]
    total: int
    limit: int
    offset: int


class AcquisitionFileCandidate(BaseModel):
    index: int
    name: str
    size_bytes: int
    progress_basis_points: int
    priority: int
    supported: bool


class AcquisitionFileSelectionRequest(BaseModel):
    file_index: int = Field(ge=0)


def validate_endpoint_url(value: HttpUrl) -> HttpUrl:
    if value.username or value.password:
        raise ValueError("Endpoint URL must not include credentials")
    if value.scheme not in {"http", "https"}:
        raise ValueError("Endpoint URL must use HTTP or HTTPS")
    return value
