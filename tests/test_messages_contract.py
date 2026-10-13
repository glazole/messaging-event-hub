"""Contract and migration shape checks for the v0.2 service publishing API."""
import uuid

import pytest
from pydantic import ValidationError
from eventhub.api import MessageIn, ServiceIn
from eventhub.models import OutboundCommand, ServiceEndpoint, ServicePublishGrant


def test_message_contract():
    request = MessageIn.model_validate({
        "connection_key": "telegram-test",
        "idempotency_key": "job-123",
        "target": {"conversation_id": "12345"},
        "message": {"type": "text", "text": "Готово"},
    })
    assert request.message.text == "Готово"
    assert request.target.conversation_id == "12345"


@pytest.mark.parametrize("message", [
    {"type": "file", "text": "abc"},
    {"type": "text", "text": ""},
    {"type": "text", "text": "x" * 4097},
])
def test_reject_invalid_messages(message):
    with pytest.raises(ValidationError):
        MessageIn.model_validate({
            "connection_key": "telegram-test",
            "idempotency_key": "job-123",
            "target": {"conversation_id": "12345"},
            "message": message,
        })


def test_publisher_only_service_and_legacy_service():
    assert ServiceIn(workspace_id=uuid.uuid4(), service_key="publisher").callback_url is None
    assert ServiceIn(workspace_id=uuid.uuid4(), service_key="echo", callback_url="http://echo:8002/events").callback_url


def test_additive_database_shapes():
    assert ServiceEndpoint.__table__.c.publish_secret_ref.nullable
    assert OutboundCommand.__table__.c.source_service_id.nullable
    assert "service_publish_grants" == ServicePublishGrant.__tablename__
