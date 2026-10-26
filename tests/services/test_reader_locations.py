"""Saved selections must be navigable and finite before entering replication."""

from copy import deepcopy

import pytest

from papyrus.core.exceptions import ValidationError
from papyrus.services.library_validation import validate_location
from papyrus.services.reader_location_validation import validate_reader_locator, validate_reader_selection

EPUB = {
    "version": 1,
    "type": "epub",
    "spineIndex": 2,
    "textOffset": 12,
    "localProgression": 0.1,
    "totalProgression": 0.25,
}
PDF = {"version": 1, "type": "pdf", "pageIndex": 3, "pageOffset": 0.2, "totalProgression": 0.4}
SELECTION = {"text": "Selected words", "start": EPUB, "end": {**EPUB, "textOffset": 26}, "rectangles": []}


def test_reader_locations_and_selections_roundtrip():
    assert validate_reader_locator(EPUB) == EPUB
    assert validate_reader_locator(PDF) == PDF
    assert validate_reader_selection(SELECTION) == SELECTION
    location = {"page_number": 1, "reader_locator": EPUB, "selection": SELECTION}
    assert validate_location(location) == location
    selection = {
        "text": "PDF words",
        "start": PDF,
        "end": PDF,
        "rectangles": [{"pageIndex": 3, "left": 0.1, "top": 0.2, "right": 0.8, "bottom": 0.3}],
    }
    assert validate_reader_selection(selection) == selection


@pytest.mark.parametrize(
    "patch",
    [
        {"version": True},
        {"version": 2},
        {"spineIndex": -1},
        {"spineIndex": False},
        {"textOffset": -1},
        {"totalProgression": float("nan")},
        {"localProgression": 2},
        {"unknown": 1},
    ],
)
def test_invalid_locators_are_rejected(patch):
    with pytest.raises(ValidationError):
        validate_reader_locator({**EPUB, **patch})


@pytest.mark.parametrize(
    "patch",
    [
        {"end": {**EPUB, "textOffset": 5}},
        {"end": PDF},
        {"text": ""},
        {"rectangles": [False]},
        {"end": {**EPUB, "spineIndex": 3}},
    ],
)
def test_invalid_selection_is_rejected(patch):
    with pytest.raises(ValidationError):
        validate_reader_selection({**deepcopy(SELECTION), **patch})


def test_selection_must_match_saved_start():
    with pytest.raises(ValidationError):
        validate_location({"page_number": 1, "reader_locator": PDF, "selection": SELECTION})
