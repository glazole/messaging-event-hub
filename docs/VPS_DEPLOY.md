# Развёртывание на VPS: proxy.handyman.moscow

Документ для отдельного Linux VPS (Нидерланды) с уже установленными Docker и Tailscale. Дополнительные Nginx, Certbot, Cloudflare Tunnel и брокеры не требуются. HTTPS обеспечивает отдельный контейнер **Caddy**; конфигурация VPS включается только через дополнительный Docker Compose файл.

## 1. Сетевая модель

```mermaid
flowchart LR
  TG[Telegram] -->|HTTPS :443| DNS[proxy.handyman.moscow]
  DNS --> CA[Caddy / TLS]
  CA -->|Только POST webhook| API[FastAPI :8000]
  API --> PG[(PostgreSQL)]
  WK[Worker] <--> PG
  WK --> TG
  ADM[Admin ПК] -->|Tailscale + SSH port-forward| SSH[VPS sshd]
  SSH -->|localhost:8000| API
```

Публичные TCP-порты только 80 и 443 (помимо уже настроенного администрирования по SSH/Tailscale). API опубликован Docker Compose **только на 127.0.0.1:8000 VPS**, БД вообще не публикуется. В Caddy разрешён лишь `POST /v1/webhooks/telegram/*`. `GET /health/live`, `/docs`, `/v1/outbound` и `/v1/connections` снаружи получают 404.

## 2. Предварительные проверки на VPS

```bash
docker --version
docker compose version
cat /etc/os-release | head -n 5
sudo ss -ltnp | grep -E ':(80|443)\\b' || true
curl -4 -s https://api.ipify.org && echo
```

Последняя команда показывает внешний IPv4 адрес VPS (не публикуйте его в открытых сообщениях без необходимости). Если 80/443 уже заняты, не запускайте Caddy до разбора конфликта. Проверьте также облачный firewall/security groups и локальный firewall. Входящие TCP 80/443 должны быть разрешены; не отключайте защиту целиком и не включайте UFW без проверки доступа по SSH/Tailscale.

## 3. DNS в Cloud.ru

У регистратора NS уже заданы как `evo-cns01.cloud.ru` и `evo-cns02.cloud.ru`. **Не изменяйте NS.**

В DNS-зоне `handyman.moscow` добавьте A-запись:
- имя `proxy` (либо полное имя, если UI ожидает FQDN);
- тип `A`;
- значение = публичный IPv4 VPS;
- TTL 300 или системное значение.

Если A-запись `proxy` уже существует, обновите **её**, не создавайте дубликат. Проверить DNS:

```bash
dig +short proxy.handyman.moscow A
dig +short @evo-cns01.cloud.ru proxy.handyman.moscow A
```

Дождитесь, пока домен указывает на публичный адрес VPS. Если A/AAAA/CNAME конфликтуют, проверьте все записи `proxy`.

## 4. Загрузка проекта и секреты

Репозиторий приватный, поэтому клонирование требует авторизации GitHub. На VPS используется отдельный **read-only Deploy Key** (`~/.ssh/id_ed25519_eventhub`), предварительно добавленный в GitHub → Settings → Deploy keys. Если репозиторий уже клонирован, повторять `git clone` не нужно.

```bash
# Проверка имеющегося Deploy Key.
ssh -i ~/.ssh/id_ed25519_eventhub -o IdentitiesOnly=yes -T git@github.com

# Только на новом сервере: клонирование приватного репозитория.
GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_eventhub -o IdentitiesOnly=yes" \
  git clone git@github.com:glazole/messaging-event-hub.git

cd ~/messaging-event-hub

# Настроить один раз для данного репозитория: после этого достаточно обычного git pull.
git config --local core.sshCommand \
  "ssh -i $HOME/.ssh/id_ed25519_eventhub -o IdentitiesOnly=yes"
git config --local --get core.sshCommand
git pull origin main
```

`core.sshCommand` хранится в локальном `.git/config` и не меняет SSH-настройки других репозиториев. Если Deploy Key ещё не создан, сначала создайте его через `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_eventhub` и загрузите **только публичный** `.pub`-ключ в GitHub.

Создайте файл окружения (существующий `.env` не перезаписываем) и сгенерируйте четыре разных секрета:

```bash
test -f .env || cp .env.example .env
chmod 600 .env

python3 - <<'PY'
import secrets

for name in [
    "POSTGRES_PASSWORD",
    "ADMIN_API_KEY",
    "CALLBACK_SHARED_KEY",
    "TELEGRAM_WEBHOOK_SECRET",
]:
    print(f"{name}={secrets.token_hex(24)}")
PY

nano .env
```

Python-команда **только выводит** новые значения; скопируйте их в `.env` вручную. `POSTGRES_PASSWORD` должен совпадать с паролем в `DATABASE_URL` (спецсимволы URL-кодируются); `ADMIN_API_KEY` защищает административный API, `CALLBACK_SHARED_KEY` — обращения к Demo Consumer, `TELEGRAM_WEBHOOK_SECRET` — входящий webhook. Токен `TELEGRAM_BOT_TOKEN` получите отдельно через BotFather. Если переменные Telegram в исходном `.env.example` закомментированы, раскомментируйте их в `.env`. Не помещайте секреты в GitHub или чаты.

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
docker compose -f docker-compose.yml -f docker-compose.vps.yml logs --tail 100 caddy migrate api worker
```

API должен отвечать **локально на VPS**:
```bash
curl -fsS http://127.0.0.1:8000/health/ready
```

После успешного DNS + TLS внешний запрос к `https://proxy.handyman.moscow/` должен вернуть **404**, потому что корневой URL преднамеренно недоступен, но TLS-сертификат должен быть действителен.

```bash
curl -I https://proxy.handyman.moscow/
```

Caddy получает и продлевает TLS-сертификат автоматически. Публичный `POST /v1/webhooks/telegram/*` проксируется в `api:8000`, остальные публичные пути возвращают 404. Правила находятся в [`deploy/Caddyfile`](../deploy/Caddyfile).

Проверьте, что Alembic создал семь прикладных таблиц ядра и восьмую служебную `alembic_version`:

```bash
docker compose exec postgres sh -c \
'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt relay.*"'
```

## 5. Безопасный доступ к Swagger/API из своего компьютера

Используйте существующий Tailscale + SSH, не публикуя админку. На **своём компьютере**:

```bash
ssh -L 8000:127.0.0.1:8000 glazole@TAILSCALE_VPS_IP
```

Откройте на компьютере `http://127.0.0.1:8000/docs` — туннель защищён SSH и Tailscale. Используйте HTTP заголовок `X-Admin-Key` из .env. Если локальный порт 8000 занят, замените первую 8000 на 18000 и откройте localhost:18000.

В API можно вручную создать workspace, Telegram connection, service и route по README. Для тестового подключения ниже это автоматизирует bootstrap-скрипт.

## 6. Привязка Telegram webhook

После получения токена Telegram внесите `TELEGRAM_BOT_TOKEN` в `.env`. Чтобы API и worker получили изменённые переменные, пересоздайте их:

```bash
nano .env
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vps.yml \
  up -d --force-recreate api worker
```

Если после запуска менялся также `CALLBACK_SHARED_KEY`, пересоздайте и `demo`.

Проверенный способ зарегистрировать все тестовые объекты и webhook одним скриптом:

```bash
python3 scripts/bootstrap_telegram.py
```

Скрипт проверяет Telegram-токен, создаёт workspace, connection, service и три маршрута (`message.received`, `file.received`, `callback.received`), вызывает Telegram `setWebhook` и проверяет `getWebhookInfo`. UUID сохраняются в `.local/telegram_bootstrap.json` (исключён из Git), чтобы при повторном запуске использовать существующие записи. Секреты берутся из `.env`, а не выводятся в консоль.

Альтернативно webhook можно зарегистрировать вручную через Telegram Bot API, указав:

```text
URL = https://proxy.handyman.moscow/v1/webhooks/telegram/CONNECTION_UUID
secret_token = TELEGRAM_WEBHOOK_SECRET
```

Метод: `setWebhook` (см. https://core.telegram.org/bots/api#setwebhook). Проводите регистрацию в защищённой консоли; не пишите Bot Token в логи GitHub Actions и общедоступные URLs. После этого сообщение в Telegram создаст строку `relay.event_log`, а для активных маршрутов — строки `relay.event_deliveries`.

## 7. Проверка состояния и эксплуатация

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml logs -f --tail=100 api worker caddy
docker compose -f docker-compose.yml -f docker-compose.vps.yml exec postgres \
  psql -U eventhub -d eventhub -c "SELECT event_type, received_at FROM relay.event_log ORDER BY received_at DESC LIMIT 10"
```

Вместо eventhub укажите свои `POSTGRES_USER` и `POSTGRES_DB`. Для просмотра именно проверенной цепочки Telegram → API → worker → Demo Consumer:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vps.yml \
  logs --tail=100 api worker demo
```

При нормальной доставке в логах есть `POST /v1/webhooks/telegram/... 200 OK`, `DEMO received message.received` и `POST http://demo:8001/events 200 OK`.

Если события есть, но ответов от бота нет, это ещё не означает неисправность ingress: нужен зарегистрированный service route и/или создание `outbound_command` бизнес-сервисом. В комплекте demo consumer принимает событие, но **не отправляет автоматический echo-ответ**. Для проверенного примера исходящей команды, поиска chat_id и проверки статуса см. [`OPERATIONS.md`](OPERATIONS.md).

Для обновления кода: `git pull`, затем снова `docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build`. Для данных PostgreSQL предусмотрен Docker named volume; перед обновлениями схемы делайте резервные копии БД.

## 8. Что не следует делать

- Не публиковать `/docs`, `/v1/workspaces`, `/v1/connections` и `/v1/outbound` в интернет.
- Не открывать наружу порты PostgreSQL 5432 и API 8000.
- Не копировать production-токены из сторонних систем и рабочих проектов.
- Не считать результатом работы бота один лишь ответ `/health/ready`: нужен функциональный тест с Telegram.
- Не отключать TLS-проверку и не ставить `0.0.0.0:8000:8000` в основном Compose.
