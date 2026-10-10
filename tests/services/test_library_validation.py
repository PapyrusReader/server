import pytest

from papyrus.core.exceptions import ValidationError
from papyrus.models import SyncAnnotation, SyncBook
from papyrus.services.library_validation import convert_value


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
