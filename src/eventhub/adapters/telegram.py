"""Telegram HTTP Bot API adapter; no Telegram dependency in the event router."""
import hmac
import httpx

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
            kind = "file.received" if ("document" in message or "photo" in message) else "message.received"
            payload = {
                "actor_id": str(message.get("from", {}).get("id", "")),
                "conversation_id": str(message.get("chat", {}).get("id", "")),
                "message_id": str(message.get("message_id", "")),
                "text": message.get("text") or message.get("caption"),
                "attachment": message.get("document") or message.get("photo"),
            }
            return kind, str(update_id), payload
        if "callback_query" in update:
            callback = update["callback_query"]
            message = callback.get("message") or {}
            return "callback.received", str(update_id), {
                "callback_id": callback.get("id"),
                "data": callback.get("data"),
                "actor_id": str(callback.get("from", {}).get("id", "")),
                "conversation_id": str(message.get("chat", {}).get("id", "")),
            }
        return "telegram.update", str(update_id), {"unsupported": True}

    @staticmethod
    async def send(token: str, command_type: str, target: dict, payload: dict):
        method = {"message.send": "sendMessage", "file.send": "sendDocument",
                  "message.edit": "editMessageText", "callback.answer": "answerCallbackQuery"}.get(command_type)
        if not method:
            raise ValueError("Unsupported command type: " + command_type)
        args = dict(payload)
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
