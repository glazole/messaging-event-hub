import pytest
from eventhub.core import resolve_secret

def test_env_secret(monkeypatch):
    monkeypatch.setenv("EXAMPLE_CREDENTIAL", "value")
    assert resolve_secret("env://EXAMPLE_CREDENTIAL") == "value"

def test_missing_secret_is_rejected():
    with pytest.raises(ValueError):
        resolve_secret("env://NOT_CONFIGURED_KEY_123")
    with pytest.raises(ValueError):
        resolve_secret(None)
    with pytest.raises(ValueError):
        resolve_secret("file:///etc/passwd")
