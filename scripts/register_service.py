#!/usr/bin/env python3
"""Register an Event Hub business service and optional publishing grants/routes.

Uses the local private admin API and the existing Telegram bootstrap state.
Requires only Python standard library. Does not create or print secrets.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from bootstrap_telegram import ROOT, load_env, local_post

STATE_PATH = ROOT / ".local" / "registered_services.json"
BOOTSTRAP_PATH = ROOT / ".local" / "telegram_bootstrap.json"
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,99}$")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return data


def save_json(path: Path, value: dict):
    # Reuse atomic file permissions and persistence from the existing bootstrap.
    import os
    import tempfile
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".service_", text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def parser():
    p = argparse.ArgumentParser(description="Register Event Hub service over private admin API")
    p.add_argument("--service-key", required=True, help="Unique service key inside workspace")
    p.add_argument("--workspace-id", help="Defaults to Telegram bootstrap workspace")
    p.add_argument("--connection-id", action="append", default=[], help="Connection UUID; repeatable")
    p.add_argument("--callback-url", help="Optional receiving service HTTP(S) endpoint")
    p.add_argument("--callback-secret-env", help="Environment variable used for callback X-Hub-Key")
    p.add_argument("--publish-secret-env", help="Environment variable containing publisher bearer token")
    p.add_argument("--grant-publish", action="store_true", help="Give explicit outbound grant per connection")
    p.add_argument("--route", action="append", default=[], help="Event type for inbound route; repeatable")
    return p


def register(args, *, post=local_post, config=None, bootstrap=None, state=None, persist=None):
    config = load_env() if config is None else config
    bootstrap = load_json(BOOTSTRAP_PATH) if bootstrap is None else bootstrap
    state = load_json(STATE_PATH) if state is None else state
    persist = (lambda: save_json(STATE_PATH, state)) if persist is None else persist

    key = args.service_key
    if not SERVICE_RE.fullmatch(key):
        raise RuntimeError("service-key must be 2..100 characters: lowercase a-z, digits, _ or -")
    admin = config.get("ADMIN_API_KEY", "")
    if len(admin) < 20:
        raise RuntimeError("ADMIN_API_KEY is absent or too short")
    workspace = args.workspace_id or bootstrap.get("workspace_id")
    if not workspace or not UUID_RE.fullmatch(workspace):
        raise RuntimeError("Specify --workspace-id or run bootstrap_telegram.py first")
    connections = list(dict.fromkeys(args.connection_id or ([bootstrap["connection_id"]] if
                       (args.grant_publish or args.route) and bootstrap.get("connection_id") else [])))
    if (args.grant_publish or args.route) and not connections:
        raise RuntimeError("Specify --connection-id or initialize Telegram bootstrap connection")
    if any(not UUID_RE.fullmatch(c) for c in connections):
        raise RuntimeError("connection-id must be a UUID")
    if args.route and not args.callback_url:
        raise RuntimeError("--route requires --callback-url (required when initially registering)")
    if args.callback_secret_env and not args.callback_url:
        raise RuntimeError("--callback-secret-env requires --callback-url")
    if args.callback_url and not args.callback_url.startswith(("http://", "https://")):
        raise RuntimeError("--callback-url must start with http:// or https://")
    if args.grant_publish and not args.publish_secret_env:
        raise RuntimeError("--grant-publish requires --publish-secret-env")
    for env_name in (args.callback_secret_env, args.publish_secret_env):
        if env_name:
            if not ENV_RE.fullmatch(env_name):
                raise RuntimeError("Secret environment variable name is invalid")
            if len(config.get(env_name, "")) < 32:
                raise RuntimeError(f"Set a random secret of >=32 characters in .env: {env_name}")
    if any(not route.strip() or len(route) > 150 for route in args.route):
        raise RuntimeError("Invalid event type")

    cache_key = f"{workspace}:{key}"
    entries = state.setdefault("services", {})
    item = entries.get(cache_key)
    expected = {
        "workspace_id": workspace,
        "service_key": key,
        "callback_url": args.callback_url,
        "auth_secret_ref": f"env://{args.callback_secret_env}" if args.callback_secret_env else None,
        "publish_secret_ref": f"env://{args.publish_secret_env}" if args.publish_secret_env else None,
    }
    if item:
        if item["definition"] != expected:
            raise RuntimeError("Existing local registration differs. No update API is available; review config manually.")
        service_id = item["id"]
        print(f"Service {key}: {service_id} (local state)")
    else:
        # On pre-existing services not registered with this utility, 409 is intentional:
        # do not guess IDs or claim success without confirming persisted state.
        result = post("/v1/services", expected, admin)
        service_id = result["id"]
        entries[cache_key] = item = {"id": service_id, "definition": expected, "grants": {}, "routes": {}}
        persist()
        print(f"Service {key}: {service_id} (created)")

    for cid in connections:
        if args.grant_publish:
            if cid not in item["grants"]:
                result = post("/v1/publish-grants", {"service_id": service_id, "connection_id": cid}, admin)
                if not result.get("enabled", True):
                    raise RuntimeError("Grant exists but disabled; manual administrator review required")
                item["grants"][cid] = result["id"]
                persist()
            print(f"Publish grant: connection={cid} id={item['grants'][cid]}")
        for event_type in dict.fromkeys(args.route):
            route_key = cid + ":" + event_type
            if route_key not in item["routes"]:
                result = post("/v1/routes", {
                    "workspace_id": workspace, "service_id": service_id,
                    "connection_id": cid, "event_type": event_type
                }, admin)
                item["routes"][route_key] = result["id"]
                persist()
            print(f"Inbound route: connection={cid}, type={event_type}, id={item['routes'][route_key]}")
    print("Registration complete. Credentials and service configuration require an API restart if changed.")


def main():
    register(parser().parse_args())


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"Registration failed: {exc}", file=sys.stderr)
        sys.exit(1)
