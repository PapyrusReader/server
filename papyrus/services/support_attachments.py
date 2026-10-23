"""Validate support media before any bytes are sent to GitHub."""

from dataclasses import dataclass

from fastapi import HTTPException, UploadFile

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_ATTACHMENTS * MAX_ATTACHMENT_BYTES + 64 * 1024
MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
}


@dataclass(frozen=True)
class SupportAttachment:
    name: str
    content_type: str
    data: bytes


def read_attachments(files: list[UploadFile]) -> list[SupportAttachment]:
    if len(files) > MAX_ATTACHMENTS:
        raise HTTPException(status_code=422, detail="Attach up to five files.")

    attachments = []

    for index, file in enumerate(files, start=1):
        extension = (file.filename or "").rsplit(".", 1)[-1].lower()
        content_type = MEDIA_TYPES.get(extension)

        if content_type is None:
            raise HTTPException(status_code=422, detail="Unsupported attachment type.")

        data = file.file.read(MAX_ATTACHMENT_BYTES + 1)

        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail="Attachments must be between 1 byte and 10 MB.")

        if not matches_media(data, extension):
            raise HTTPException(status_code=422, detail="Invalid attachment content.")

        attachments.append(SupportAttachment(f"attachment-{index}.{extension}", content_type, data))

    return attachments


def matches_media(data: bytes, extension: str) -> bool:
    match extension:
        case "png":
            return data.startswith(b"\x89PNG\r\n\x1a\n")
        case "jpg" | "jpeg":
            return data.startswith(b"\xff\xd8\xff")
        case "gif":
            return data.startswith((b"GIF87a", b"GIF89a"))
        case "webp":
            return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        case "mp4" | "mov":
            return len(data) >= 12 and data[4:8] == b"ftyp"
        case "webm":
            return data.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in data[:4096]
        case _:
            return False
