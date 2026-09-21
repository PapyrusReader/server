"""Requests for account-independent OPDS resource retrieval."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class OpdsCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(max_length=1024, pattern=r"^[^:\r\n]*$")
    password: SecretStr = Field(max_length=4096)


class OpdsRelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=8192)
    catalog_url: str = Field(min_length=1, max_length=8192)
    max_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=256 * 1024 * 1024)
    credentials: OpdsCredentials | None = None
