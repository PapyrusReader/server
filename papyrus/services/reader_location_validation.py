"""Validate durable EPUB text anchors and PDF page geometry."""

from typing import Any

from papyrus.core.exceptions import ValidationError
from papyrus.services.library_validation import finite_number


def _index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**31


def _fraction(value: Any) -> bool:
    return finite_number(value) and 0 <= value <= 1


def _valid_pdf_rectangle(rectangle: Any, start_page: int, end_page: int) -> bool:
    if not isinstance(rectangle, dict):
        return False

    if rectangle.keys() != {"pageIndex", "left", "top", "right", "bottom"}:
        return False

    page_index = rectangle["pageIndex"]

    if not _index(page_index) or not start_page <= page_index <= end_page:
        return False

    if any(not _fraction(rectangle[key]) for key in ("left", "top", "right", "bottom")):
        return False

    return not (rectangle["left"] >= rectangle["right"] or rectangle["top"] >= rectangle["bottom"])


def validate_reader_locator(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValidationError("Unsupported reader locator version")

    kind = value.get("type")
    common = {"version", "type", "totalProgression"}

    if kind == "epub":
        allowed = common | {"spineIndex", "localProgression", "textOffset", "anchor"}
        valid = _index(value.get("spineIndex")) and _fraction(value.get("localProgression"))
        offset = value.get("textOffset")
        anchor = value.get("anchor")
        valid = valid and (offset is None or _index(offset)) and (anchor is None or isinstance(anchor, str))
    elif kind == "pdf":
        allowed = common | {"pageIndex", "pageOffset"}
        valid = _index(value.get("pageIndex")) and _fraction(value.get("pageOffset"))
    else:
        raise ValidationError("Unsupported reader locator type")

    if value.keys() - allowed or not valid or not _fraction(value.get("totalProgression")):
        raise ValidationError("Invalid reader locator")

    return value


def validate_reader_selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() - {"text", "start", "end", "rectangles"}:
        raise ValidationError("Invalid reader selection")

    if not isinstance(value.get("text"), str) or not value["text"].strip():
        raise ValidationError("Reader selection requires text")

    start = validate_reader_locator(value.get("start"))
    end = validate_reader_locator(value.get("end"))
    rectangles = value.get("rectangles", [])

    if start["type"] != end["type"] or not isinstance(rectangles, list):
        raise ValidationError("Invalid reader selection range")

    if start["type"] == "epub":
        if (
            start["spineIndex"] != end["spineIndex"]
            or not _index(start.get("textOffset"))
            or not _index(end.get("textOffset"))
            or start["textOffset"] >= end["textOffset"]
            or rectangles
        ):
            raise ValidationError("Invalid EPUB selection range")
    elif start["pageIndex"] > end["pageIndex"] or not rectangles:
        raise ValidationError("Invalid PDF selection range")

    for rectangle in rectangles:
        if not _valid_pdf_rectangle(rectangle, start["pageIndex"], end["pageIndex"]):
            raise ValidationError("Invalid PDF selection rectangle")

    return value
