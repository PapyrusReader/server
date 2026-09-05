import pytest

from papyrus.core.exceptions import ValidationError
from papyrus.models import SyncAnnotation, SyncBook
from papyrus.services.library_validation import convert_value, normalize_book_payload


@pytest.mark.parametrize(
    ("column", "value"),
    [
        (SyncBook.__table__.c.series_number, 10**500),
        (SyncBook.__table__.c.publication_date, "0001-01-01T00:00:00+01:00"),
        (SyncAnnotation.__table__.c.location, {"page_number": 1, "percentage": 10**500}),
    ],
)
def test_overflows_are_controlled_validation_errors(column, value):
    with pytest.raises(ValidationError):
        convert_value(column, value)


def test_legacy_invalid_values_remain_in_envelope_without_blocking_queue():
    envelope = {"is_physical": None, "series_number": "invalid", "file_size": 10**500, "lent_to": "Reader"}
    normalized = normalize_book_payload({"custom_metadata": envelope})
    assert normalized == {"custom_metadata": envelope, "lent_to": "Reader"}


def test_explicit_promoted_value_wins_and_stays_subject_to_validation():
    normalized = normalize_book_payload({"custom_metadata": {"is_physical": True}, "is_physical": None})
    assert normalized["is_physical"] is None
    with pytest.raises(ValidationError):
        convert_value(SyncBook.__table__.c.is_physical, normalized["is_physical"])
