from pydantic import BaseModel, ConfigDict, Field


class MetadataSearch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    q: str = Field(min_length=1, max_length=300)
    page: int = Field(default=1, ge=1, le=10000)
    isbn: bool = False


class MetadataResult(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    authors: list[str] | None = None
    publisher: str | None = None
    published_date: str | None = None
    description: str | None = None
    cover_url: str | None = None
    language: str | None = None
    isbn: str | None = None
    isbn13: str | None = None
    page_count: int | None = None


class MetadataPage(BaseModel):
    results: list[MetadataResult]
    total_results: int | None = Field(default=None, ge=0)
