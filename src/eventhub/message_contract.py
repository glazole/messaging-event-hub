"""Versioned transport-neutral message contract; platform adapters translate it."""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, field_validator, model_validator


class InlineButton(BaseModel):
    text: str = Field(min_length=1, max_length=80)
    callback_data: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def validate_action(self):
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("Exactly one of callback_data and url is required")
        if self.callback_data is not None and not 1 <= len(self.callback_data.encode("utf-8")) <= 64:
            raise ValueError("callback_data must be 1..64 UTF-8 bytes")
        if self.url is not None:
            parsed = urlsplit(self.url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("Button URL must be absolute HTTPS without credentials")
        return self


InlineKeyboard = Annotated[
    list[Annotated[list[InlineButton], Field(min_length=1, max_length=8)]],
    Field(min_length=1, max_length=16),
]


class TelegramFile(BaseModel):
    type: Literal["telegram_file_id"]
    file_id: str = Field(min_length=1)


class HttpsFile(BaseModel):
    type: Literal["https_url"]
    url: str = Field(min_length=9, max_length=2048)

    @field_validator("url")
    @classmethod
    def https_only(cls, value: str):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Media URL must be absolute HTTPS without credentials")
        return value


class StorageRef(BaseModel):
    type: Literal["storage_ref"]
    storage_key: str = Field(min_length=1, max_length=100)
    object_key: str = Field(min_length=1, max_length=1024)
    version_id: str | None = Field(default=None, max_length=255)


MediaSource = Annotated[TelegramFile | HttpsFile | StorageRef, Field(discriminator="type")]


class MediaObject(BaseModel):
    kind: Literal["document", "photo", "video", "audio", "voice", "animation"]
    source: MediaSource
    caption: str | None = Field(default=None, max_length=1024)
    parse_mode: Literal["HTML", "MarkdownV2"] | None = None


class TextMessage(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=4096)
    parse_mode: Literal["HTML", "MarkdownV2"] | None = None
    buttons: InlineKeyboard | None = None


class MediaMessage(BaseModel):
    type: Literal["media"]
    media: MediaObject
    buttons: InlineKeyboard | None = None


class EditTextMessage(BaseModel):
    type: Literal["edit_text"]
    message_id: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4096)
    parse_mode: Literal["HTML", "MarkdownV2"] | None = None
    buttons: InlineKeyboard | None = None


class CallbackAnswerMessage(BaseModel):
    type: Literal["callback_answer"]
    callback_id: str = Field(min_length=1)
    text: str = Field(default="", max_length=200)
    show_alert: bool = False


OutgoingMessage = Annotated[
    TextMessage | MediaMessage | EditTextMessage | CallbackAnswerMessage,
    Field(discriminator="type"),
]


class MessageTarget(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=255)


class MessageIn(BaseModel):
    connection_key: str = Field(min_length=2, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=150)
    target: MessageTarget
    message: OutgoingMessage


def queue_command(message: OutgoingMessage) -> tuple[str, dict]:
    """Convert API DTO to durable worker command; reject unsupported storage backends."""
    if isinstance(message, TextMessage):
        payload = {"text": message.text}
        if message.parse_mode:
            payload["parse_mode"] = message.parse_mode
        if message.buttons:
            payload["reply_markup"] = {"inline_keyboard": [[b.model_dump(exclude_none=True) for b in row] for row in message.buttons]}
        return "message.send", payload
    if isinstance(message, MediaMessage):
        if isinstance(message.media.source, StorageRef):
            # Keep the durable storage_ref contract stable, but do not silently queue a
            # command that this release cannot resolve. A resolver is needed first.
            raise ValueError("storage_ref media requires a configured storage resolver")
        media = message.media.model_dump(exclude_none=True)
        payload = {"kind": media["kind"], "source": media["source"]}
        for key in ("caption", "parse_mode"):
            if key in media:
                payload[key] = media[key]
        if message.buttons:
            payload["reply_markup"] = {"inline_keyboard": [[b.model_dump(exclude_none=True) for b in row] for row in message.buttons]}
        return "media.send", payload
    if isinstance(message, EditTextMessage):
        payload = {"message_id": message.message_id, "text": message.text}
        if message.parse_mode:
            payload["parse_mode"] = message.parse_mode
        if message.buttons:
            payload["reply_markup"] = {"inline_keyboard": [[b.model_dump(exclude_none=True) for b in row] for row in message.buttons]}
        return "message.edit", payload
    if isinstance(message, CallbackAnswerMessage):
        return "callback.answer", {"callback_query_id": message.callback_id, "text": message.text, "show_alert": message.show_alert}
    raise ValueError("Unsupported message type")
