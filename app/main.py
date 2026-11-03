"""Failed-order alert service.

Endpoints:
  POST /events                 generic events from your own integration system
  POST /webhooks/deliverect    Deliverect order / status webhooks
  POST /webhooks/urbanpiper    UrbanPiper order / status webhooks
  POST /test-alert             send a test message to every configured channel
  GET  /alerts                 recent alerts
  GET  /health
"""
import asyncio
import contextlib
import hmac
import logging

from fastapi import FastAPI, HTTPException, Request

from .config import Settings
from .notifiers import Notifier, format_alert
from .parsers import OrderEvent, parse_deliverect, parse_generic, parse_urbanpiper
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("alerts")


class Service:
    def __init__(self, settings: Settings, notifier: Notifier | None = None):
        self.s = settings
        self.store = Store(settings.db_path)
        self.notifier = notifier or Notifier(settings)

    async def handle(self, ev: OrderEvent) -> dict:
        self.store.upsert(ev.source, ev.order_id, display_id=ev.display_id, channel=ev.channel,
                          store=ev.store, status=ev.status)
        if ev.kind == "acknowledged":
            self.store.mark_acked(ev.source, ev.order_id)
        elif ev.kind == "failed":
            return {"alerted": await self.alert(ev.source, ev.order_id, "failed", ev.reason)}
        elif ev.kind == "cancelled":
            # A cancelled order should not also trigger the "not acknowledged" alarm.
            self.store.mark_acked(ev.source, ev.order_id)
            if self.s.alert_on_cancel:
                return {"alerted": await self.alert(ev.source, ev.order_id, "cancelled", ev.reason)}
        return {"alerted": False}

    async def alert(self, source, order_id, reason, detail=None) -> bool:
        if not self.store.claim_alert(source, order_id, reason):
            return False  # already alerted for this order
        order = self.store.get(source, order_id)
        subject, body = format_alert(order, reason, detail, self.s.ack_timeout_min)
        log.warning("ALERT %s %s/%s", reason, source, order_id)
        await self.notifier.send(subject, body)
        return True

    async def check_overdue(self) -> int:
        n = 0
        for o in self.store.overdue(self.s.ack_timeout_min * 60):
            if await self.alert(o["source"], o["order_id"], "not_acknowledged"):
                n += 1
        return n

    async def watchdog(self):
        while True:
            try:
                await self.check_overdue()
            except Exception:
                log.exception("watchdog error")
            await asyncio.sleep(self.s.watchdog_interval_s)


def create_app(settings: Settings | None = None, notifier: Notifier | None = None, run_watchdog=True) -> FastAPI:
    settings = settings or Settings()
    svc = Service(settings, notifier)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(svc.watchdog()) if run_watchdog else None
        log.info("channels enabled: %s", svc.notifier.enabled_channels() or "NONE")
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Failed Order Alerts", lifespan=lifespan)
    app.state.svc = svc

    def auth(req: Request):
        if not settings.ingest_token:
            return
        given = req.headers.get("x-alert-token") or req.query_params.get("token") or ""
        if not hmac.compare_digest(given, settings.ingest_token):
            raise HTTPException(401, "bad token")

    async def ingest(req: Request, parse):
        auth(req)
        try:
            payload = await req.json()
            ev = parse(payload)
        except ValueError as e:
            raise HTTPException(400, str(e))
        res = await svc.handle(ev)
        return {"ok": True, "order_id": ev.order_id, "kind": ev.kind, **res}

    @app.post("/events")
    async def events(req: Request):
        return await ingest(req, parse_generic)

    @app.post("/webhooks/deliverect")
    async def deliverect(req: Request):
        return await ingest(req, lambda p: parse_deliverect(p, settings))

    @app.post("/webhooks/urbanpiper")
    async def urbanpiper(req: Request):
        return await ingest(req, lambda p: parse_urbanpiper(p, settings))

    @app.post("/test-alert")
    async def test_alert(req: Request):
        auth(req)
        res = await svc.notifier.send("Test alert", "✅ Test alert from Failed Order Alerts")
        return {"channels": res}

    @app.get("/alerts")
    async def alerts(req: Request):
        auth(req)
        return svc.store.recent_alerts()

    @app.get("/health")
    async def health():
        return {"ok": True, "channels": svc.notifier.enabled_channels()}

    return app


app = create_app()
