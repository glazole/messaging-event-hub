"""Registration tool unit tests; no live admin API or database required."""
import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from register_service import register


WID = "11111111-1111-4111-8111-111111111111"
CID = "22222222-2222-4222-8222-222222222222"
SID = "33333333-3333-4333-8333-333333333333"
GID = "44444444-4444-4444-8444-444444444444"
RID = "55555555-5555-4555-8555-555555555555"


def args(**extra):
    data = {
        "service_key": "test-sender", "workspace_id": None, "connection_id": [],
        "callback_url": None, "callback_secret_env": None,
        "publish_secret_env": "TEST_SENDER_PUBLISH_KEY",
        "grant_publish": True, "route": [],
    }
    data.update(extra)
    return argparse.Namespace(**data)


def test_registration_is_repeatable():
    calls = []
    state = {}
    def post(path, body, key):
        calls.append(path)
        assert key == "a" * 24
        return {"id": {"/v1/services": SID, "/v1/publish-grants": GID}[path], "enabled": True}
    kwargs = dict(
        post=post,
        config={"ADMIN_API_KEY":"a" * 24, "TEST_SENDER_PUBLISH_KEY":"z" * 64},
        bootstrap={"workspace_id": WID, "connection_id": CID},
        state=state, persist=lambda: None,
    )
    register(args(), **kwargs)
    register(args(), **kwargs)
    assert calls == ["/v1/services", "/v1/publish-grants"]
    assert state["services"][f"{WID}:test-sender"]["grants"][CID] == GID


def test_routes_registered_once():
    calls = []
    state = {}
    def post(path, body, _admin):
        calls.append(path)
        return {"id": SID if path.endswith("services") else RID}
    params = args(callback_url="http://processor:8000/events",
                  callback_secret_env="CALLBACK_KEY", route=["file.received"],
                  grant_publish=False, publish_secret_env=None)
    kw = dict(post=post, state=state, persist=lambda: None,
              bootstrap={"workspace_id":WID,"connection_id":CID},
              config={"ADMIN_API_KEY":"a" * 24,"CALLBACK_KEY":"b" * 64})
    register(params, **kw)
    register(params, **kw)
    assert calls == ["/v1/services", "/v1/routes"]


def test_refuses_missing_secret_before_creating_service():
    calls = []
    with pytest.raises(RuntimeError, match="TEST_SENDER_PUBLISH_KEY"):
        register(args(), post=lambda *a: calls.append(a),
                 config={"ADMIN_API_KEY":"a" * 24},
                 bootstrap={"workspace_id":WID,"connection_id":CID},
                 state={}, persist=lambda: None)
    assert not calls
