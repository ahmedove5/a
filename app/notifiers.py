"""Alert channels. Each channel is enabled only if its env vars are set."""
import asyncio
import html
import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import Settings

log = logging.getLogger("alerts")

REASONS = {
    "failed": "❌ Order FAILED / الطلب فشل",
    "not_acknowledged": "⏰ Not acknowledged by store / الطلب ما وصل للفرع",
    "cancelled": "🚫 Order CANCELLED / الطلب انلغى",
}


def format_alert(order: dict, reason: str, detail: str | None, timeout_min: float) -> tuple[str, str]:
    title = REASONS.get(reason, reason)
    lines = [
        title,
        f"Order: {order.get('display_id') or order['order_id']}",
        f"Order ID: {order['order_id']}",
        f"Source: {order['source']}",
        f"Channel: {order.get('channel') or '-'}",
        f"Store: {order.get('store') or '-'}",
        f"Status: {order.get('status') or '-'}",
    ]
    if reason == "not_acknowledged":
        lines.append(f"No acknowledgement after {timeout_min:g} min")
    if detail:
        lines.append(f"Detail: {detail}")
    return title, "\n".join(lines)


class Notifier:
    def __init__(self, s: Settings, client: httpx.AsyncClient | None = None):
        self.s = s
        self.client = client or httpx.AsyncClient(timeout=10)

    def enabled_channels(self) -> list[str]:
        ch = []
        if self.s.telegram_token and self.s.telegram_chat_ids:
            ch.append("telegram")
        if self.s.smtp_host and self.s.email_to:
            ch.append("email")
        if self.s.chat_webhook_urls:
            ch.append("webhook")
        return ch

    async def send(self, subject: str, body: str) -> dict[str, bool]:
        tasks = {}
        if self.s.telegram_token and self.s.telegram_chat_ids:
            tasks["telegram"] = self._telegram(body)
        if self.s.smtp_host and self.s.email_to:
            tasks["email"] = asyncio.to_thread(self._email, subject, body)
        if self.s.chat_webhook_urls:
            tasks["webhook"] = self._webhooks(body)
        if not tasks:
            log.warning("No alert channel configured; alert only logged:\n%s", body)
            return {}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        out = {}
        for name, res in zip(tasks, results):
            ok = not isinstance(res, Exception)
            if not ok:
                log.error("alert via %s failed: %r", name, res)
            out[name] = ok
        return out

    async def _telegram(self, body: str):
        url = f"https://api.telegram.org/bot{self.s.telegram_token}/sendMessage"
        for chat_id in self.s.telegram_chat_ids:
            r = await self.client.post(url, json={"chat_id": chat_id, "text": f"<pre>{html.escape(body)}</pre>",
                                                  "parse_mode": "HTML"})
            r.raise_for_status()

    def _email(self, subject: str, body: str):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.s.email_from or self.s.smtp_user
        msg["To"] = ", ".join(self.s.email_to)
        msg.set_content(body)
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if self.s.smtp_user:
                smtp.login(self.s.smtp_user, self.s.smtp_password)
            smtp.send_message(msg)

    async def _webhooks(self, body: str):
        for url in self.s.chat_webhook_urls:
            r = await self.client.post(url, json={"text": body})
            r.raise_for_status()
