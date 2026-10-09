# Эксплуатация и испытания

## 1. Настройка

1. Создать `.env` из `.env.example`.
2. Использовать длинные случайные `POSTGRES_PASSWORD`, `ADMIN_API_KEY` и `CALLBACK_SHARED_KEY`. Обновить пароль также внутри `DATABASE_URL`. При специальных символах пароль в URL необходимо URL-кодировать.
3. Для Telegram задать `TELEGRAM_BOT_TOKEN` и `TELEGRAM_WEBHOOK_SECRET` (допустимые Telegram secret_token символы: A-Z, a-z, 0-9, _ и -; длина 1–256).
4. Поднять Compose: `docker compose up --build -d`.
5. Проверить: `docker compose ps`, `docker compose logs migrate`, `curl localhost:8000/health/ready`.

## 2. Проверка без токена

Создать workspace, connection, demo service и event route, как показано в README; использовать endpoint `POST /v1/events` и посмотреть `docker compose logs demo worker`. Это подтверждает цепочку от admin API до durable queue и HTTP consumer, без Telegram API.

## 3. Подключение Telegram

Для webhook нужен внешний HTTPS сервер, доступный Telegram. Проксируйте наружу **только** `/v1/webhooks/telegram/`, остальные маршруты должны быть закрыты.

Пример запроса (не кладите токен в историю shell, в демонстрации используется переменная):

```bash
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=https://YOUR_DOMAIN/v1/webhooks/telegram/CONNECTION_UUID" \
  --data-urlencode "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

Секрет проверяется из `X-Telegram-Bot-Api-Secret-Token`. Регистрация webhook и Token должны соответствовать **одному и тому же** Telegram-боту. Приложение отвечает 200 лишь после commit событий и доставок.

### Функциональная проверка входящего и исходящего Telegram

После `python3 scripts/bootstrap_telegram.py` отправьте боту `/start` и проверьте API, worker и Demo Consumer:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vps.yml \
  logs --tail=100 api worker demo
```

Ожидаются HTTP 200 для Telegram webhook и Demo Consumer. Найдите `chat_id` в сохранённых событиях:

```bash
docker compose exec -T postgres \
  psql -U eventhub -d eventhub -c \
  "SELECT event_type,
          payload->>'conversation_id' AS chat_id,
          received_at
   FROM relay.event_log
   ORDER BY received_at DESC
   LIMIT 5;"
```

Чтобы отправить ответ **через очередь Event Hub**, в следующей команде замените `TELEGRAM_CHAT_ID` на полученный `chat_id`:

```bash
python3 - <<'PY'
import sys
import uuid

sys.path.insert(0, "scripts")
from bootstrap_telegram import load_env, read_state, local_post

chat_id = "TELEGRAM_CHAT_ID"  # замените на результат SQL-запроса

result = local_post(
    "/v1/outbound",
    {
        "connection_id": read_state()["connection_id"],
        "command_type": "message.send",
        "idempotency_key": f"telegram-test-{uuid.uuid4()}",
        "target": {"conversation_id": chat_id},
        "payload": {
            "text": "Messaging Event Hub: исходящая доставка работает."
        },
    },
    load_env()["ADMIN_API_KEY"],
)

print(result)
PY
```

`local_post` обращается к закрытому локальному административному API и берёт его ключ из `.env`. Ответ `{"command_id":"…","created":true}` подтверждает **постановку в очередь**, а не доставку. В данной конструкции `python3 - <<'PY'` не следует использовать `input()`: стандартный ввод занят heredoc.

Проверка фактической отправки:

```bash
docker compose exec -T postgres \
  psql -U eventhub -d eventhub -c \
  "SELECT id, command_type, status, attempts,
          provider_message_id, last_error
   FROM relay.outbound_commands
   ORDER BY available_at DESC
   LIMIT 5;"
```

Ожидаются `status=succeeded`, заполненный `provider_message_id` и полученное сообщение в Telegram. Если статус `retry` или `dead`, смотрите `last_error` и логи worker.

## 4. Очередь и диагностика

Внутри контейнера PostgreSQL:

```bash
docker compose exec postgres psql -U eventhub -d eventhub -c \
  "SELECT status,count(*) FROM relay.event_deliveries GROUP BY status"
docker compose exec postgres psql -U eventhub -d eventhub -c \
  "SELECT status,count(*) FROM relay.outbound_commands GROUP BY status"
```

Вместо `eventhub` подставить свои POSTGRES_USER/POSTGRES_DB. `dead` требует разбирательства; автоматического re-drive нет. Изменение статусов вручную без понимания идемпотентности способно привести к повторной отправке.

## 5. Миграции

```bash
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic history
docker compose run --rm migrate alembic upgrade head
```

Схему генерировать автоматически без проверки диффа нельзя. Сделайте бэкап PostgreSQL перед изменением существующей production базы.

## 6. Масштабирование и безопасность

Для MVP callback_url задаёт доверенный администратор; worker может обращаться к локальным HTTP сервисам. В SaaS перед запуском необходимы ограничения DNS/IP и egress policy против SSRF; RBAC, отдельные ключи на tenant/service, ограничение размера payload, rate limiting и схема ротации ключей. Не предоставляйте доступ к admin API внешним пользователям.

Воркеры сканируют доступные строки `pending/retry` и просроченные `processing`. При временной ошибке экспоненциальная задержка ограничена 300 сек. Безопасные повторные попытки требуют идемпотентного downstream. Отправка Telegram не гарантирует отсутствия дубликатов после сетевого разрыва.

## 7. Развитие

- MAX как отдельный inbound/outbound adapter после появления легитимного токена;
- Mini App и Mini App auth как отдельные сервисы;
- аудит, metrics, tenant RBAC, DLQ с re-drive, durable media storage;
- интеграционные тесты на поднятом PostgreSQL и тестовом Telegram-боте.
