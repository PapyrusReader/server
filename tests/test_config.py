import pytest
from pydantic import ValidationError


def test_rejects_default_secret_key():
    from papyrus.config import Settings
    with pytest.raises(ValidationError):
        Settings(secret_key="change-me-in-production-use-openssl-rand-hex-32")
