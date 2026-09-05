"""PowerSync upload schemas."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


BOOK_UPLOAD_FIELDS |= frozenset(
    {
        "publication_date",
        "file_format",
        "file_size",
        "file_hash",
        "is_physical",
        "physical_location",
        "lent_to",
        "lent_at",
        "series_id",
        "series_name",
        "series_number",
        "started_at",
        "completed_at",
        "last_read_at",
    }
)
ENTITY_FIELDS = frozenset({"owner_user_id", "created_at", "updated_at"})
UPLOAD_FIELDS = {
    "books": BOOK_UPLOAD_FIELDS,
    "shelves": ENTITY_FIELDS
    | {
        "name",
        "description",
        "color_hex",
        "icon_code_point",
        "icon_font_family",
        "icon_font_package",
        "icon_match_text_direction",
        "parent_shelf_id",
        "is_smart",
        "smart_query",
        "sort_order",
    },
    "tags": ENTITY_FIELDS | {"name", "color_hex", "description"},
    "notes": ENTITY_FIELDS | {"book_id", "title", "content", "location", "tags", "is_pinned"},
    "annotations": ENTITY_FIELDS | {"book_id", "selected_text", "color", "location", "note"},
    "book_shelves": {"owner_user_id", "book_id", "shelf_id", "added_at", "sort_order"},
    "book_tags": {"owner_user_id", "book_id", "tag_id", "created_at"},
}


class PowerSyncCrudMutation(BaseModel):
    """One owned library mutation uploaded from the PowerSync queue."""

    model_config = ConfigDict(populate_by_name=True)

    table: Literal["books", "shelves", "tags", "notes", "annotations", "book_shelves", "book_tags"] = Field(
        alias="type"
    )
    op: Literal["PUT", "PATCH", "DELETE", "put", "patch", "delete"]
    id: str
    op_id: int | None = Field(default=None, alias="op_id")
    tx_id: int | None = None
    op_data: dict[str, Any] | None = Field(default=None, alias="data")

    @model_validator(mode="after")
    def reject_unknown_fields(self) -> "PowerSyncCrudMutation":
        unknown = (self.op_data or {}).keys() - UPLOAD_FIELDS[self.table]

        if unknown:
            raise ValueError(f"Unsupported {self.table} fields: {', '.join(sorted(unknown))}")

        return self


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
