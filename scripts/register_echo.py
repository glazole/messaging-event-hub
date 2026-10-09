#!/usr/bin/env python3
"""Register an independent Echo consumer for the existing Telegram lab connection."""
import sys

from bootstrap_telegram import load_env, local_post, read_state, save_state


def main():
    state = read_state()
    for key in ("workspace_id", "connection_id"):
        if not state.get(key):
            raise RuntimeError("Run scripts/bootstrap_telegram.py first")
    admin = load_env().get("ADMIN_API_KEY", "")
    if not admin:
        raise RuntimeError("ADMIN_API_KEY is missing in .env")

    if not state.get("echo_service_id"):
        result = local_post(
            "/v1/services",
            {
                "workspace_id": state["workspace_id"],
                "service_key": "echo-service",
                "callback_url": "http://echo:8002/events",
                "auth_secret_ref": "env://CALLBACK_SHARED_KEY",
            },
            admin,
        )
        state["echo_service_id"] = result["id"]
        save_state(state)
        print("Echo service registered:", result["id"])
    else:
        print("Echo service exists:", state["echo_service_id"])

    if not state.get("echo_route_id"):
        result = local_post(
            "/v1/routes",
            {
                "workspace_id": state["workspace_id"],
                "service_id": state["echo_service_id"],
                "connection_id": state["connection_id"],
                "event_type": "message.received",
            },
            admin,
        )
        state["echo_route_id"] = result["id"]
        save_state(state)
        print("Echo route registered:", result["id"])
    else:
        print("Echo route exists:", state["echo_route_id"])
    print("Send a new text message to Telegram. Old events are not replayed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
