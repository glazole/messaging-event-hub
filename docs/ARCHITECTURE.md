# Архитектура Messaging Event Hub (v0.1)

## v0.2: внешние сервисы и защищённая публикация

Входящий Telegram webhook остаётся на голландском VPS. Внутренний Event Hub API,
PostgreSQL и Worker также остаются там. Внешние бизнес-сервисы (позже обработчик
файлов на отдельном VPS со своим S3) смогут получать события через callback по
закрытой сети и публиковать команды через `POST /v1/messages` с сервисным
Bearer-токеном. Права определяет `relay.service_publish_grants`; идентификатор
отправителя фиксируется в `outbound_commands.source_service_id`. Детали и
миграция: [SERVICE_MESSAGES_API.md](SERVICE_MESSAGES_API.md).

## Границы компонентов

- **Core** управляет идентификацией подключений, регистрацией событий, маршрутизацией, очередями, повторами и журналом.
- **Channel adapter** отвечает за платформенный формат, проверку webhook, канонизацию и реализацию команд платформы.
- **Business service** получает единообразный HTTP event envelope; не импортирует Telegram/MAX SDK.
- **Mini App** впоследствии работает с собственным Business API; platform-specific init-data проходит проверку в отдельном auth adapter, не в Event Hub.
- **MAX** пока архитектурная точка расширения: регистрация connection не означает, что MAX адаптер действует.

## Развёртывание

```mermaid
flowchart TB
  subgraph External
    TG[Telegram Bot API]
  end
  RP[HTTPS reverse proxy] --> API[FastAPI Event Hub]
  TG --> RP
  API --> PG[(PostgreSQL)]
  W[Queue worker] <--> PG
  W --> D[Demo/Business Service]
  W --> TG
  A[Local Admin] --> API
  MIG[Alembic migration container] --> PG
```

Административные endpoints должны быть доступны только из защищённой сети. В docker-compose порт API проброшен на 127.0.0.1, публичного reverse proxy в комплекте нет.

## ER диаграмма

```mermaid
erDiagram
  workspaces ||--o{ channel_connections : owns
  workspaces ||--o{ service_endpoints : owns
  channel_connections ||--o{ event_log : produces
  service_endpoints ||--o{ event_routes : receives
  channel_connections o|--o{ event_routes : selects
  event_log ||--o{ event_deliveries : schedules
  event_routes ||--o{ event_deliveries : binds
  service_endpoints ||--o{ event_deliveries : consumes
  channel_connections ||--o{ outbound_commands : sends
  workspaces ||--o{ outbound_commands : owns
  workspaces {
    uuid id PK
    varchar code UK
    varchar name
    timestamptz created_at
  }
  channel_connections {
    uuid id PK
    uuid workspace_id FK
    varchar connection_key
    varchar provider_key
    varchar display_name
    varchar credential_ref
    varchar webhook_secret_ref
    varchar external_account_id
    jsonb settings
    boolean is_enabled
  }
  service_endpoints {
    uuid id PK
    uuid workspace_id FK
    varchar service_key
    text callback_url
    varchar auth_secret_ref
    boolean is_enabled
    int timeout_seconds
  }
  event_routes {
    uuid id PK
    uuid workspace_id FK
    uuid service_id FK
    uuid connection_id FK
    varchar event_type
    boolean is_enabled
  }
  event_log {
    uuid id PK
    uuid workspace_id FK
    uuid connection_id FK
    varchar event_type
    int schema_version
    varchar idempotency_key
    uuid correlation_id
    varchar provider_event_id
    jsonb payload
    jsonb raw_payload
    timestamptz received_at
  }
  event_deliveries {
    uuid id PK
    uuid event_id FK
    uuid route_id FK
    uuid service_id FK
    varchar status
    int attempts
    int max_attempts
    timestamptz available_at
    varchar lease_owner
    timestamptz lease_until
    varchar last_error
    timestamptz completed_at
  }
  outbound_commands {
    uuid id PK
    uuid workspace_id FK
    uuid connection_id FK
    varchar idempotency_key
    uuid correlation_id
    varchar command_type
    jsonb target
    jsonb payload
    varchar status
    int attempts
    int max_attempts
    timestamptz available_at
    varchar lease_owner
    timestamptz lease_until
    varchar last_error
    varchar provider_message_id
    timestamptz sent_at
  }
```

Таблицы находятся в схеме `relay`; Alembic revision `0001`. Основные FK заданы в SQLAlchemy. В API дополнительные tenant-проверки проверяют принадлежность выбранного service/connection одному workspace при создании маршрута.

## Схема последовательности

```mermaid
sequenceDiagram
    participant T as Telegram
    participant A as Adapter / API
    participant P as PostgreSQL
    participant W as Worker
    participant S as Business Service

    T->>A: POST /webhooks/telegram/UUID
    A->>A: secret + update_id validation
    A->>P: BEGIN, INSERT event, INSERT deliveries
    P-->>A: COMMIT
    A-->>T: HTTP 200

    W->>P: claim deliveries (SKIP LOCKED)
    P-->>W: job leased
    W->>S: POST event + X-Delivery-Id
    S-->>W: HTTP 2xx
    W->>P: status=succeeded

    S->>A: POST /v1/outbound
    A->>P: INSERT outbound command
    W->>P: claim command
    W->>T: sendMessage via Bot API
    T-->>W: message id
    W->>P: status=succeeded
```

## Гарантии и риски

**At-least-once delivery**: если worker завершился между отправкой HTTP и фиксацией успеха, доставка повторится. Бизнес-сервис обязан дедуплицировать по `X-Delivery-Id` и сохранять его вместе с результатом одной транзакцией. Аналогично исходящая команда может быть доставлена в Telegram повторно при сбое между ответом Telegram и записью результата в БД; никакого exactly-once обещания нет.

**Лизинг**: worker берёт очередь через `SKIP LOCKED` и отбирает записи `pending/retry` либо `processing` с истёкшим lease. Lease 60 секунд, HTTP timeout меньше. Долгие callback-операции должны отвечать быстро, продолжая работу асинхронно.

**Секреты**: `env://NAME` — ссылки на конфигурацию среды. Нет общего секрета на все подключения, а незаданные ключи приводят к отказу. Сырой платформенный payload может содержать персональные данные, поэтому доступ к БД, сроки хранения и резервное копирование требуют отдельной политики.

**Доступность**: одна PostgreSQL инсталляция и один worker подходят для лабораторной нагрузки. Несколько воркеров допустимы благодаря блокировкам, но нагрузочные, аварийные и миграционные тесты обязательны.

**События**: тип `message.received` не зависит от Telegram; `telegram.update` фиксирует неизвестный тип платформенного update для дальнейшей обработки. Канонизация Telegram поддерживает обычные текстовые сообщения, документы/фото и callback. Остальные типы не преобразованы.

**Ограничения текущего MVP**: нет API просмотра очередей, административного re-drive, редактора правил, полноценного observability, безопасного ограничения callback destinations, secret rotation и управления правами клиентов. Не следует выставлять admin API в публичный интернет. Отсутствует MAX outbound.
