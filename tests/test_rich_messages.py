import pytest
from pydantic import ValidationError

from eventhub.message_contract import MessageIn, queue_command, InlineButton
from eventhub.adapters.telegram import TelegramAdapter


def request(message):
    return MessageIn.model_validate({
        "connection_key": "telegram-test",
        "idempotency_key": "msg-123",
        "target": {"conversation_id": "123456"},
        "message": message,
    })


def test_text_buttons():
    msg = request({"type":"text","text":"Выберите",
                   "buttons":[[{"text":"Запустить","callback_data":"start"},
                               {"text":"Сайт","url":"https://example.org"}]]})
    kind, payload = queue_command(msg.message)
    assert kind == "message.send"
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "start"


@pytest.mark.parametrize("bad", [
    {"text":"broken"},
    {"text":"a","callback_data":"x"*65},
    {"text":"a","url":"http://example.org"},
])
def test_invalid_button(bad):
    with pytest.raises(ValidationError):
        InlineButton.model_validate(bad)


@pytest.mark.parametrize("kind,telegram_method", [
    ("document","sendDocument"), ("photo","sendPhoto"), ("video","sendVideo"),
    ("audio","sendAudio"), ("voice","sendVoice"), ("animation","sendAnimation"),
])
def test_media_kind_maps_to_command(kind, telegram_method):
    msg = request({"type":"media","media":{
        "kind":kind,"source":{"type":"telegram_file_id","file_id":"abc"}}})
    command, payload = queue_command(msg.message)
    assert command == "media.send"
    assert payload["kind"] == kind


def test_storage_ref_schema_reserved_but_not_yet_dispatchable():
    msg = request({"type":"media","media":{
        "kind":"document","source":{"type":"storage_ref",
                                    "storage_key":"results","object_key":"jobs/a.xlsx"}}})
    with pytest.raises(ValueError, match="storage resolver"):
        queue_command(msg.message)


def test_callback_and_edit():
    edit = request({"type":"edit_text","message_id":12,"text":"Готово"})
    assert queue_command(edit.message)[0] == "message.edit"
    callback = request({"type":"callback_answer","callback_id":"abc","text":"Запущено"})
    assert queue_command(callback.message)[1]["callback_query_id"] == "abc"


def test_inbound_photo_and_callback():
    kind, _, payload = TelegramAdapter.normalize({"update_id":11,"message":{
        "message_id":7,"chat":{"id":123},"from":{"id":456},
        "photo":[{"file_id":"a"},{"file_id":"b"}],"caption":"Привет"}})
    assert kind == "file.received"
    assert payload["attachments"][0]["metadata"]["file_id"] == "b"
    assert payload["text"] == "Привет"
    kind, _, payload = TelegramAdapter.normalize({"update_id":12,"callback_query":{
        "id":"query1","from":{"id":456},"data":"yes",
        "message":{"message_id":7,"chat":{"id":123}}}})
    assert kind == "callback.received"
    assert payload["message_id"] == "7"
