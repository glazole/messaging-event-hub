"""Telegram adapter: provider-specific conversions stay outside business contracts."""
import hmac
import httpx


MEDIA_FIELDS = ("document", "photo", "video", "audio", "voice", "animation", "video_note", "sticker")


class TelegramAdapter:
    provider_key = "telegram"

    @staticmethod
    def valid_secret(expected: str, provided: str | None) -> bool:
        return bool(expected) and bool(provided) and hmac.compare_digest(expected, provided)

    @staticmethod
    def normalize(update: dict) -> tuple[str, str, dict]:
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise ValueError("Telegram update_id обязателен")
        if "message" in update:
            message = update["message"]
            attachments = []
            for field in MEDIA_FIELDS:
                if field not in message:
                    continue
                entry = message[field]
                if field == "photo" and isinstance(entry, list):
                    entry = entry[-1] if entry else {}
                attachments.append({"kind": field, "provider": "telegram", "metadata": entry})
            payload = {
                "actor_id": str(message.get("from", {}).get("id", "")),
                "conversation_id": str(message.get("chat", {}).get("id", "")),
                "message_id": str(message.get("message_id", "")),
                "text": message.get("text") or message.get("caption"),
                "attachment": message.get("document") or message.get("photo"),
                "attachments": attachments,
                "reply_to_message_id": str(message.get("reply_to_message", {}).get("message_id", "")) or None,
            }
            return ("file.received" if attachments else "message.received"), str(update_id), payload
        if "callback_query" in update:
            callback = update["callback_query"]
            message = callback.get("message") or {}
            return "callback.received", str(update_id), {
                "callback_id": callback.get("id"),
                "data": callback.get("data"),
                "actor_id": str(callback.get("from", {}).get("id", "")),
                "conversation_id": str(message.get("chat", {}).get("id", "")),
                "message_id": str(message.get("message_id", "")),
                "schema_version": 2,
            }
        return "telegram.update", str(update_id), {"unsupported": True}

    @staticmethod
    async def send(token: str, command_type: str, target: dict, payload: dict):
        methods = {"message.send": "sendMessage", "file.send": "sendDocument",
                   "message.edit": "editMessageText", "callback.answer": "answerCallbackQuery"}
        if command_type == "media.send":
            methods["media.send"] = {
                "document": "sendDocument", "photo": "sendPhoto", "video": "sendVideo",
                "audio": "sendAudio", "voice": "sendVoice", "animation": "sendAnimation"
            }.get(payload.get("kind"))
        method = methods.get(command_type)
        if not method:
            raise ValueError("Unsupported command type: " + command_type)
        args = dict(payload)
        args.pop("kind", None)
        if command_type == "media.send":
            src = args.pop("source")
            if src["type"] == "storage_ref":
                raise ValueError("Storage resolver not yet configured")
            if src["type"] not in ("telegram_file_id", "https_url"):
                raise ValueError("Unsupported media source")
            field = {"sendDocument":"document","sendPhoto":"photo","sendVideo":"video",
                     "sendAudio":"audio","sendVoice":"voice","sendAnimation":"animation"}[method]
            args[field] = src["file_id"] if src["type"] == "telegram_file_id" else src["url"]
        if method != "answerCallbackQuery":
            if "conversation_id" not in target:
                raise ValueError("target.conversation_id required")
            args["chat_id"] = target["conversation_id"]
        elif "callback_query_id" not in args:
            args["callback_query_id"] = target.get("callback_id")
        if method == "sendDocument" and "document" not in args:
            raise ValueError("file.send needs a Telegram document file_id or HTTPS URL")
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"https://api.telegram.org/bot{token}/{method}", json=args)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError("Telegram API rejected command: " + str(data.get("description", "")))
            return data.get("result", {})
