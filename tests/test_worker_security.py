from eventhub.worker import redact_secret
import logging


def test_token_removed_from_error():
    token = "123456:secret-token"
    error = f"Client error '401 Unauthorized' for url 'https://api.telegram.org/bot{token}/sendMessage'"
    result = redact_secret(error, token)
    assert token not in result
    assert "[REDACTED]" in result


def test_httpx_url_logging_disabled():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
