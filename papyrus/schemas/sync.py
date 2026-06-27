"""PowerSync upload schemas."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BOOK_UPLOAD_FIELDS = frozenset(
    {
        "title",
        "subtitle",
        "author",
        "co_authors",
        "isbn",
        "isbn13",
        "publisher",
        "language",
        "page_count",
        "description",
        "cover_image_url",
        "file_media_id",
        "cover_media_id",
        "reading_status",
        "current_page",
        "current_position",
        "current_cfi",
        "is_favorite",
        "rating",
        "custom_metadata",
        "added_at",
        "owner_user_id",
        "updated_at",
    }
)


class PowerSyncCrudMutation(BaseModel):
    """Single books-table mutation uploaded from the PowerSync queue."""

    model_config = ConfigDict(populate_by_name=True)

    table: Literal["books"] = Field(alias="type")
    op: Literal["PUT", "PATCH", "DELETE", "put", "patch", "delete"]
    id: str
    op_id: int | None = Field(default=None, alias="op_id")
    tx_id: int | None = None
    op_data: dict[str, Any] | None = Field(default=None, alias="data")

    @field_validator("op_data")
    @classmethod
    def reject_unknown_book_fields(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        unknown = value.keys() - BOOK_UPLOAD_FIELDS
        if unknown:
            raise ValueError(f"Unsupported book fields: {', '.join(sorted(unknown))}")
        return value


class PowerSyncUploadRequest(BaseModel):
    """One PowerSync CRUD transaction."""

    batch: list[PowerSyncCrudMutation]


class PowerSyncUploadResponse(BaseModel):
    """Summary of an applied PowerSync upload transaction."""

    applied_count: int


class FileStorageSettings(BaseModel):
    """Public file storage capability advertised by this server."""

    supported: bool
    quota_bytes: int


class DataSyncSettingsResponse(BaseModel):
    """Public sync settings used by clients for custom server discovery."""

    data_sync_url: str
    file_storage: FileStorageSettings
