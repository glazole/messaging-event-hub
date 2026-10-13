# Service Messaging API v0.2 — contract and rollout

This is an additive update. The Dutch VPS remains the Telegram webhook and Event Hub host.
Business services can run on another VM later; **no file worker or S3 is deployed in this change**.

## Service-scoped publishing

`POST /v1/messages` (internal/private endpoint), `Authorization: Bearer <SERVICE_TOKEN>`,
`Content-Type: application/json`:

```json
{
  "connection_key": "telegram-test",
  "idempotency_key": "i17-validation-98765",
  "target": {"conversation_id": "123456789"},
  "message": {"type": "text", "text": "Проверка файла завершена"}
}
```

Returns HTTP 202: `{"command_id":"UUID","created":true}`. This is **queued**, not delivered.
Same service, connection and idempotency key with identical content returns `created:false`.
Reusing a key with changed content returns HTTP 409. The key is scoped to the service.
The contract now supports typed messages: text, media (document/photo/video/audio/voice/animation),
inline keyboards, edit_text, and callback_answer. Media sources include telegram_file_id and
HTTPS URLs. The storage_ref format is reserved for future S3 integration but is deliberately
rejected with HTTP 422 until a secure storage resolver is implemented.

### Rich message payloads

Inline keyboard:

```json
{"connection_key":"telegram-test","idempotency_key":"menu-001",
 "target":{"conversation_id":"123456789"},
 "message":{"type":"text","text":"Выберите действие",
  "buttons":[[{"text":"Запустить","callback_data":"run:123"},
              {"text":"Открыть","url":"https://example.org"}]]}}
```

File/photo/video/audio/voice/animation:

```json
{"connection_key":"telegram-test","idempotency_key":"report-001",
 "target":{"conversation_id":"123456789"},
 "message":{"type":"media","media":{"kind":"document",
  "source":{"type":"telegram_file_id","file_id":"TELEGRAM_FILE_ID"},
  "caption":"Результат проверки"}}}
```

Supported source alternatives:
`{"type":"https_url","url":"https://example.org/report.pdf"}` (Telegram fetches the
HTTPS asset; never assume a private S3 URL is accessible), or the **future**
`{"type":"storage_ref","storage_key":"results","object_key":"jobs/123/report.pdf",
"version_id":"optional"}`. No local filesystem paths, arbitrary s3:// strings, credentials,
or uploading bytes through Event Hub API.

Callback acknowledgement (callback_id comes from received `callback.received`):

```json
{"connection_key":"telegram-test","idempotency_key":"cb-42",
 "target":{"conversation_id":"123456789"},
 "message":{"type":"callback_answer","callback_id":"CALLBACK_ID",
            "text":"Принято","show_alert":false}}
```

Message edit:

```json
{"connection_key":"telegram-test","idempotency_key":"edit-42",
 "target":{"conversation_id":"123456789"},
 "message":{"type":"edit_text","message_id":77,"text":"Готово"}}
```

`callback_data` must be 1-64 UTF-8 bytes; buttons may contain HTTPS links.
Out-of-scope for this release: albums, reply keyboards, media editing, reactions,
polls, message deletion, file bytes ingestion, storage resolver and larger uploads.
Media limits remain provider-dependent. No capability fallback: unsupported media
must fail explicitly rather than silently be sent as text.

### Inbound normalized events

`message.received` for text. `file.received` for recognized Telegram media.
Legacy `payload.attachment` is retained, alongside a new `attachments` array:
`[{"kind":"document","provider":"telegram","metadata":{"file_id":"..."}}]`.
For Telegram photos, the largest entry is selected. These are Telegram metadata,
not S3 objects and not downloaded file bytes. The original Telegram payload stays in
`event_log.raw_payload`.
`callback.received` contains `callback_id`, `data`, `actor_id`,
`conversation_id`, and `message_id`. The receiving business service decides
whether and when to issue `callback_answer` / `edit_text`.
Existing routes by event type continue to work; new file consumer needs a
`file.received` route. No events are replayed on route creation.

**Provider constraints:** Telegram file IDs are bot-specific. An HTTP URL must be
reachable by Telegram; security policies should allowlist media hosting origins
before exposing remote-VM integrations. This release does not automatically
fetch user-submitted Telegram media or push it into S3.

The service is identified from a confidential Bearer secret stored in environment variables
and referenced from `relay.service_endpoints.publish_secret_ref`. Never send a service_key
in the body, embed a credential in JSON, commit tokens, or expose the admin API.

## Registration (private admin API)

A service may be registered without any callback URL if it only publishes messages.
Existing demo/echo services, routes, and callbacks remain untouched.

```http
POST /v1/services
X-Admin-Key: <ADMIN_API_KEY>
Content-Type: application/json

{"workspace_id":"WORKSPACE_UUID","service_key":"file-processor",
 "callback_url":"https://processor.internal.example/events",
 "auth_secret_ref":"env://FILE_PROCESSOR_CALLBACK_KEY",
 "publish_secret_ref":"env://FILE_PROCESSOR_PUBLISH_KEY"}
```

For a publisher-only service set `callback_url` to null and omit `auth_secret_ref`.
To enable outbound messages, explicitly grant each connection:

```http
POST /v1/publish-grants
X-Admin-Key: <ADMIN_API_KEY>
Content-Type: application/json

{"service_id":"SERVICE_UUID","connection_id":"CONNECTION_UUID"}
```

Grant is idempotent; it does not automatically re-enable a disabled grant. Grant rows
can be disabled in DB by an administrator pending a dedicated revoke API.

Add a strong, randomly generated secret to the **Event Hub API container environment**
(e.g. `FILE_PROCESSOR_PUBLISH_KEY`); the remote service must hold the same secret.
The Event Hub never stores the raw credential in PostgreSQL; only `env://...`.
Secrets must be rotated by an administrator with coordinated deployment.
For the current lab, avoid sharing the existing `CALLBACK_SHARED_KEY` as a publisher token.

**Networking:** only Telegram webhook is published at
`https://proxy.handyman.moscow/v1/webhooks/telegram/{connection_id}`.
The Caddy allowlist must remain unchanged. `/v1/messages` MUST NOT be opened
to the internet as-is. A future remote VM requires a private overlay
(Tailscale/WireGuard), HTTPS/mTLS or equivalent trusted link, and IP/egress controls.
Use a private-network ingress to the Event Hub API on the Dutch VPS.

## Backward compatibility and database migration

- Existing `POST /v1/service-replies` and `POST /v1/outbound` keep working.
- Existing `src/eventhub/echo.py` and registered Telegram webhook are unchanged.
- `relay.service_endpoints.publish_secret_ref` is nullable (all old rows NULL).
- New `relay.service_publish_grants` table is empty until admin grants access.
- `relay.outbound_commands.source_service_id` is nullable (old rows stay NULL).
- **No DROP, DELETE, TRUNCATE or bulk rewrite** of existing records.
- Alembic revision `0002` is additive, safely checks schema elements in case an
  existing installation or a fresh one already has them (initial revision uses metadata).
- Migration downgrade is intentionally blocked instead of destroying attribution.

### Recommended guarded deployment

Before deployment, take a PostgreSQL backup and record counts of `relay.event_log`,
`relay.event_deliveries`, `relay.outbound_commands`, `relay.service_endpoints`, and
`relay.channel_connections`. Review Alembic current revision (must be `0001`
on existing deployment). Test migration against a **restored copy** before production.

After deploying reviewed code:

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml run --rm migrate alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build api worker
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
```

Verify current revision `0002`, unchanged counts of all existing data,
Telegram webhook receipt, legacy Echo route, new grant and publishing behavior.
Do not run `docker compose down -v` on the VPS: it deletes DB volumes.
Avoid enabling grants before the new token has been securely provisioned.

### Inspect publishing

```sql
SELECT s.service_key, c.connection_key, g.is_enabled
FROM relay.service_publish_grants g
JOIN relay.service_endpoints s ON s.id=g.service_id
JOIN relay.channel_connections c ON c.id=g.connection_id;

SELECT id, source_service_id, command_type, status, last_error, sent_at
FROM relay.outbound_commands
ORDER BY available_at DESC LIMIT 20;
```

`status='succeeded'` with `provider_message_id` means Telegram accepted the send,
not that the end user read it.

## Planned separate VM: file business module

Later: the file module registers as a distinct service in the same workspace,
receives normalized events via `POST /events` over a private network,
downloads/stores/processes files with its own S3, and publishes results through
`POST /v1/messages` using its own Bearer token and explicit connection grant.
S3 file ingestion, storage_ref resolution, callback auth hardening, and reverse
proxy private ingress require separate implementation. The media/button JSON
contract itself is now part of Event Hub v0.2.
Telegram webhook stays on the Dutch VPS at its existing URL.

## Security notes

This is a transitional laboratory implementation, not full public multi-tenant SaaS:
service credential references use environment variables, grants are configured via
admin API, and v0.2 currently has no token management UI, rate limits, audit API or
automatic secret rotation. Protect network access to all non-webhook endpoints.
