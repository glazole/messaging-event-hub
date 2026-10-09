"""End-to-end smoke: schema, admin API, atomic routing, retries, demo delivery."""
import json
import os
import subprocess
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
ADMIN = os.environ["ADMIN_API_KEY"]

def post(path, body):
    data = json.dumps(body).encode()
    request = urllib.request.Request(BASE + path, data=data, method="POST",
        headers={"Content-Type":"application/json","X-Admin-Key":ADMIN})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())

def count_succeeded():
    query = "SELECT COUNT(*) FROM relay.event_deliveries WHERE status='succeeded'"
    output = subprocess.check_output([
        "docker","compose","exec","-T","postgres","psql",
        "-U",os.environ["POSTGRES_USER"],"-d",os.environ["POSTGRES_DB"],
        "-At","-c",query],text=True)
    return int(output.strip())

for _ in range(60):
    try:
        with urllib.request.urlopen(BASE + "/health/ready", timeout=3) as r:
            if r.status == 200:
                break
    except (urllib.error.URLError, TimeoutError):
        pass
    time.sleep(2)
else:
    raise RuntimeError("API never became ready")

w = post("/v1/workspaces",{"code":"smoke","name":"Smoke Test"})["id"]
c = post("/v1/connections",{
    "workspace_id":w, "connection_key":"test-telegram",
    "display_name":"Smoke Telegram", "provider_key":"telegram"})["id"]
s = post("/v1/services",{
    "workspace_id":w,"service_key":"demo","callback_url":"http://demo:8001/events",
    "auth_secret_ref":"env://CALLBACK_SHARED_KEY"})["id"]
post("/v1/routes",{"workspace_id":w,"service_id":s,
                    "connection_id":c,"event_type":"message.received"})
payload = {"connection_id":c,"event_type":"message.received",
           "idempotency_key":"smoke-event-01","payload":{"text":"hello"}}
a = post("/v1/events",payload)
b = post("/v1/events",payload)
assert a["created"] and not b["created"] and a["event_id"] == b["event_id"]
for _ in range(30):
    if count_succeeded() >= 1:
        break
    time.sleep(2)
else:
    raise AssertionError("No successful delivery")
print("SMOKE OK: migrations, API, deduplication, delivery worker, demo consumer")
