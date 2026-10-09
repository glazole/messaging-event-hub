#!/usr/bin/env python3
"""Однократная/возобновляемая настройка Telegram для тестового Event Hub.

Запуск на VPS из корня репозитория: python3 scripts/bootstrap_telegram.py
Работает на стандартной библиотеке Python. Не печатает токены/секреты.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".local"
STATE_PATH = STATE_DIR / "telegram_bootstrap.json"
API_ROOT = "http://127.0.0.1:8000"
PUBLIC_ROOT = "https://proxy.handyman.moscow"


def load_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        raise RuntimeError("Нет .env: создайте его из .env.example")
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Неправильный формат .local/telegram_bootstrap.json")
    return data


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=STATE_DIR, prefix=".telegram_bootstrap_", text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(tmp_name, STATE_PATH)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def local_post(endpoint: str, body: dict, admin_key: str) -> dict:
    request = Request(
        API_ROOT + endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Admin-Key": admin_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"Локальный API: HTTP {exc.code}, {detail}") from None
    except URLError:
        raise RuntimeError("Локальный API недоступен на 127.0.0.1:8000") from None


def telegram_post(token: str, method: str, args: dict) -> dict:
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(args).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"Telegram Bot API: HTTP {exc.code} ({method})") from None
    except URLError:
        raise RuntimeError(f"Нет соединения с Telegram Bot API ({method})") from None
    if not result.get("ok"):
        # Не печатаем исходный ответ: не должны случайно логировать чувствительные данные.
        raise RuntimeError(f"Telegram не выполнил {method}; проверьте токен и параметры")
    return result.get("result", {})


def ensure_id(state: dict, key: str, endpoint: str, body: dict, admin_key: str) -> str:
    if key not in state:
        data = local_post(endpoint, body, admin_key)
        state[key] = data["id"]
        save_state(state)
        print(f"  {key}: {state[key]} (создан)")
    else:
        print(f"  {key}: {state[key]} (из локального файла)")
    return state[key]


def main() -> int:
    cfg = load_env()
    admin_key = cfg.get("ADMIN_API_KEY", "")
    bot_token = cfg.get("TELEGRAM_BOT_TOKEN", "")
    webhook_secret = cfg.get("TELEGRAM_WEBHOOK_SECRET", "")
    callback_key = cfg.get("CALLBACK_SHARED_KEY", "")
    if len(admin_key) < 20 or not bot_token or not callback_key:
        raise RuntimeError("Заполните ADMIN_API_KEY, TELEGRAM_BOT_TOKEN и CALLBACK_SHARED_KEY в .env")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", webhook_secret):
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET должен состоять из A-Z/a-z/0-9/_/- (1..256)")
    print("Проверка Telegram-токена...")
    # Не выводить имя токена и его значение.
    telegram_post(bot_token, "getMe", {})
    print("Telegram токен действителен.")
    state = read_state()
    print("Регистрация объектов ядра:")
    wid = ensure_id(
        state, "workspace_id", "/v1/workspaces",
        {"code": "telegram-lab", "name": "Telegram Test Lab"}, admin_key,
    )
    cid = ensure_id(
        state, "connection_id", "/v1/connections",
        {
            "workspace_id": wid,
            "connection_key": "telegram-test",
            "display_name": "Telegram test connection",
            "provider_key": "telegram",
            "credential_ref": "env://TELEGRAM_BOT_TOKEN",
            "webhook_secret_ref": "env://TELEGRAM_WEBHOOK_SECRET",
        },
        admin_key,
    )
    sid = ensure_id(
        state, "service_id", "/v1/services",
        {
            "workspace_id": wid,
            "service_key": "demo-consumer",
            "callback_url": "http://demo:8001/events",
            "auth_secret_ref": "env://CALLBACK_SHARED_KEY",
        },
        admin_key,
    )
    for event_type in ("message.received", "file.received", "callback.received"):
        key = "route_" + event_type.replace(".", "_")
        ensure_id(
            state, key, "/v1/routes",
            {
                "workspace_id": wid,
                "service_id": sid,
                "connection_id": cid,
                "event_type": event_type,
            },
            admin_key,
        )
    webhook_url = f"{PUBLIC_ROOT}/v1/webhooks/telegram/{cid}"
    print("Регистрация Telegram webhook...")
    telegram_post(
        bot_token,
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": webhook_secret,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        },
    )
    info = telegram_post(bot_token, "getWebhookInfo", {})
    if info.get("url") != webhook_url:
        raise RuntimeError("getWebhookInfo вернул другой webhook URL")
    print("\nГотово. URL:", webhook_url)
    print("pending_update_count:", info.get("pending_update_count", 0))
    if info.get("last_error_message"):
        print("Последняя ошибка доставки Telegram:", info["last_error_message"])
    print("\nОтправьте боту /start или текстовое сообщение.")
    print("Проверьте: docker compose -f docker-compose.yml -f docker-compose.vps.yml logs --tail=100 api worker demo")
    print("Результат — приём события. Demo Consumer пока не отправляет ответ в Telegram.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
