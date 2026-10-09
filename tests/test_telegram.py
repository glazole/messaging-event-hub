import pytest
from eventhub.adapters.telegram import TelegramAdapter

def test_message_normalization():
    kind, key, payload = TelegramAdapter.normalize({
      "update_id": 42, "message": {"message_id": 7,"chat":{"id":123},
      "from":{"id":456},"text":"hello"}})
    assert kind == "message.received"
    assert key == "42"
    assert payload["conversation_id"] == "123"
    assert payload["text"] == "hello"

def test_document_normalization():
    kind, _, data = TelegramAdapter.normalize({
      "update_id": 9, "message": {"document":{"file_id":"file"},
      "message_id": 4,"chat":{"id":1}}})
    assert kind == "file.received"
    assert data["attachment"]["file_id"] == "file"

def test_callback_normalization():
    kind, key, payload = TelegramAdapter.normalize({
      "update_id": 10, "callback_query": {"id":"cb","from":{"id":3},
      "message":{"chat":{"id":4}},"data":"yes"}})
    assert (kind,key,payload["data"]) == ("callback.received","10","yes")

def test_reject_unidentified():
    with pytest.raises(ValueError):
        TelegramAdapter.normalize({"message":{"text":"hi"}})

def test_secret_fail_closed():
    assert TelegramAdapter.valid_secret("secret","secret")
    assert not TelegramAdapter.valid_secret("secret","other")
    assert not TelegramAdapter.valid_secret("","")
