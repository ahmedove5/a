import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def enabled_channels(self):
        return ["fake"]

    async def send(self, subject, body):
        self.sent.append((subject, body))
        return {"fake": True}


@pytest.fixture
def env(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.ingest_token = "secret"
    s.ack_timeout_min = 5
    n = FakeNotifier()
    app = create_app(s, n, run_watchdog=False)
    return TestClient(app, headers={"x-alert-token": "secret"}), n, app.state.svc


def test_rejects_bad_token(env):
    c, _, _ = env
    r = c.post("/events", json={"order_id": "1", "kind": "failed"}, headers={"x-alert-token": "x"})
    assert r.status_code == 401


def test_failed_alerts_once(env):
    c, n, _ = env
    for _ in range(3):
        c.post("/events", json={"order_id": "A1", "kind": "failed", "store": "Riyadh-1", "reason": "POS timeout"})
    assert len(n.sent) == 1
    assert "Riyadh-1" in n.sent[0][1] and "POS timeout" in n.sent[0][1]


def test_unacknowledged_triggers_watchdog(env):
    c, n, svc = env
    c.post("/events", json={"order_id": "B1", "kind": "received"})
    c.post("/events", json={"order_id": "B2", "kind": "received"})
    c.post("/events", json={"order_id": "B2", "kind": "acknowledged"})
    svc.store._db.execute("UPDATE orders SET received_at = ?", (time.time() - 600,))
    assert asyncio.run(svc.check_overdue()) == 1
    assert "B1" in n.sent[0][1] and "Not acknowledged" in n.sent[0][1]
    assert asyncio.run(svc.check_overdue()) == 0  # no repeat


def test_cancel_does_not_trigger_watchdog(env):
    c, n, svc = env
    c.post("/events", json={"order_id": "C1", "kind": "received"})
    c.post("/events", json={"order_id": "C1", "kind": "cancelled"})
    svc.store._db.execute("UPDATE orders SET received_at = ?", (time.time() - 600,))
    assert asyncio.run(svc.check_overdue()) == 0
    assert n.sent == []


def test_deliverect_failed_status(env):
    c, n, _ = env
    r = c.post("/webhooks/deliverect", json={"_id": "d1", "channelOrderDisplayId": "TLB-55",
                                            "status": 120, "channel": 7, "locationName": "Jeddah"})
    assert r.json()["kind"] == "failed"
    assert "TLB-55" in n.sent[0][1]


def test_urbanpiper_flow(env):
    c, n, _ = env
    r = c.post("/webhooks/urbanpiper", json={"order": {"details": {"id": 9, "channel": "zomato"},
                                                       "store": {"name": "Dubai Mall"}}})
    assert r.json()["kind"] == "received"
    r = c.post("/webhooks/urbanpiper", json={"order_id": 9, "new_state": "Acknowledged"})
    assert r.json()["kind"] == "acknowledged"
    assert n.sent == []


def test_bad_payload(env):
    c, _, _ = env
    assert c.post("/events", json={"kind": "failed"}).status_code == 400


def test_notifier_telegram_and_webhook():
    import httpx
    from app.notifiers import Notifier

    calls = []
    transport = httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(200, json={"ok": True}))
    s = Settings()
    s.telegram_token, s.telegram_chat_ids = "T", ["111", "222"]
    s.chat_webhook_urls = ["https://hooks.example/x"]
    s.smtp_host = ""
    res = asyncio.run(Notifier(s, httpx.AsyncClient(transport=transport)).send("subj", "body <b>"))
    assert res == {"telegram": True, "webhook": True}
    assert len(calls) == 3
    assert "botT/sendMessage" in str(calls[0].url) and b"&lt;b&gt;" in calls[0].content


def test_deliverect_real_orders_list(env):
    import json, pathlib
    c, n, _ = env
    payload = json.loads((pathlib.Path(__file__).parent / "fixtures/deliverect_orders.json").read_text())
    r = c.post("/webhooks/deliverect", json=payload).json()
    assert [o["kind"] for o in r["orders"]] == ["acknowledged"] * 3
    assert n.sent == []


def test_deliverect_stuck_before_pos(env):
    c, n, svc = env
    r = c.post("/webhooks/deliverect", json={"_id": "x1", "channelOrderDisplayId": "777", "status": 1,
                                            "channel": 53, "courier": {"deliveryBy": "Keeta"}})
    assert r.json()["kind"] == "received"
    svc.store._db.execute("UPDATE orders SET received_at = ?", (time.time() - 600,))
    assert asyncio.run(svc.check_overdue()) == 1
    assert "Keeta (53)" in n.sent[0][1] and "777" in n.sent[0][1]


def test_status_set_strips_quotes(monkeypatch):
    from app.config import _set
    monkeypatch.setenv("X_STATES", '"ACKNOWLEDGED,FOOD READY"')
    assert _set("X_STATES", "") == {"ACKNOWLEDGED", "FOOD READY"}
