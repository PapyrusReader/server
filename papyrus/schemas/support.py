"""Public support submissions contain only text explicitly supplied by the user."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SupportRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    kind: Literal["bug", "feature"]
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=10, max_length=8000)
    app_version: str = Field(min_length=1, max_length=80)
    platform: str = Field(min_length=1, max_length=80)


class SupportResponse(BaseModel):
    number: int = Field(gt=0)
    url: str
