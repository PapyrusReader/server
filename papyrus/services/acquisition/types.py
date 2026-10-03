"""Acquisition integration and job value types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from papyrus.models.acquisition import AcquisitionJob


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    title: str
    download_url: str
    protocol: str
    indexer: str
    size_bytes: int | None
    seeders: int | None
    publish_date: datetime | None
    format_hints: list[str]


@dataclass(frozen=True, slots=True)
class ReleaseTokenPayload:
    endpoint_id: UUID
    owner_user_id: UUID
    title: str
    download_url: str
    protocol: str
    indexer: str
    size_bytes: int | None
    seeders: int | None
    publish_date: datetime | None
    format_hints: list[str]


@dataclass(frozen=True, slots=True)
class BatchSubmissionResult:
    index: int
    job: AcquisitionJob | None
    error: str | None


@dataclass(frozen=True, slots=True)
class QbittorrentTorrent:
    hash: str
    state: str
    progress_basis_points: int
    downloaded_bytes: int
    total_bytes: int
    download_speed_bytes_per_second: int
    eta_seconds: int | None


@dataclass(frozen=True, slots=True)
class QbittorrentFile:
    index: int
    name: str
    size_bytes: int
    progress_basis_points: int
    priority: int


@dataclass(frozen=True, slots=True)
class JobFileCandidate:
    index: int
    name: str
    size_bytes: int
    progress_basis_points: int
    priority: int
    supported: bool
