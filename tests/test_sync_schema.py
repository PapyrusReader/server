"""Upload validation does not require a service or database session."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from papyrus.schemas.sync import PowerSyncCrudMutation


def test_book_mutation_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PowerSyncCrudMutation(
            type="books",
            op="PUT",
            id=str(uuid4()),
            data={"title": "Book", "unexpected": "value"},
        )
