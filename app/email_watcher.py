"""Watch a mailbox for Deliverect alert emails (e.g. "Order injection failed") and forward them.

Read-only on the mailbox: messages are fetched with BODY.PEEK, so they stay unread for humans.
De-duplication is by Message-ID in SQLite.

Run inside the API service (automatic when IMAP_HOST is set) or standalone:
    python -m app.email_watcher            # loop forever
    python -m app.email_watcher --once     # one pass (good for cron)
"""
import asyncio
import email
import email.header
import email.utils
import html
import imaplib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from html.parser import HTMLParser

from .config import Settings
from .notifiers import Notifier
from .store import Store

log = logging.getLogger("email_watcher")

# Subject keyword -> alert title. First match wins. Keys are lowercase.
ALERT_TYPES = [
    ("injection", "❌ Order not sent to POS / الطلب ما وصل للفرع"),
    ("failed", "❌ Order FAILED / طلب فاشل"),
    ("cancel", "🚫 Order cancelled / طلب ملغي"),
    ("delay", "⏰ Order delayed / طلب متأخر"),
    ("store closed", "🔒 Store closed / الفرع مقفل"),
    ("busy", "🔒 Store busy / الفرع مشغول"),
    ("menu", "📋 Menu publish issue / مشكلة نشر المنيو"),
]
FIELD_KEYS = r"order(?:\s*(?:id|number|#))?|channel(?:\s*order\s*id)?|location|store|branch|brand|reason|error|status|pos|time|date|customer\s*name"
FIELD_RE = re.compile(rf"^\s*({FIELD_KEYS})\s*[:\-–]\s*(.+?)\s*$", re.I | re.M)
OBJECT_ID_RE = re.compile(r"\b[0-9a-f]{24}\b")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" : ")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(s: str) -> str:
    p = _Text()
    p.feed(s)
    text = html.unescape("".join(p.parts))
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip(" :") for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def body_text(msg: Message) -> str:
    plain, htm = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = txt
        elif ctype == "text/html" and htm is None:
            htm = txt
    return (plain if plain and plain.strip() else html_to_text(htm or "")).strip()


def decode_subject(msg: Message) -> str:
    return str(email.header.make_header(email.header.decode_header(msg.get("Subject", "")))).strip()


def classify(subject: str, body: str) -> str:
    hay = f"{subject}\n{body[:300]}".lower()
    for key, title in ALERT_TYPES:
        if key in hay:
            return title
    return "🔔 Deliverect alert"


def format_email_alert(msg: Message) -> str:
    subject = decode_subject(msg)
    body = body_text(msg)
    lines = [classify(subject, body), f"Subject: {subject}"]
    fields = []
    for k, v in FIELD_RE.findall(body):
        item = f"{k.strip().title()}: {v[:200]}"
        if item not in fields:
            fields.append(item)
    lines += fields[:15]
    ids = [i for i in dict.fromkeys(OBJECT_ID_RE.findall(body)) if all(i not in f for f in fields)]
    if ids:
        lines.append("Deliverect ID: " + ", ".join(ids[:5]))
    if not fields:  # format not recognized -> forward the text itself
        excerpt = body[:1200] + ("…" if len(body) > 1200 else "")
        lines += ["", excerpt]
    return "\n".join(lines)


def _age_minutes(msg: Message) -> float:
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except Exception:
        return 0.0


class EmailWatcher:
    def __init__(self, s: Settings, store: Store, notifier: Notifier, imap_factory=None):
        self.s, self.store, self.notifier = s, store, notifier
        self.imap_factory = imap_factory or (lambda: imaplib.IMAP4_SSL(s.imap_host, s.imap_port))

    def _fetch(self) -> list[Message]:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")
        crit = f'(SINCE "{since}")'
        if self.s.imap_from_filter:
            crit = f'(SINCE "{since}" FROM "{self.s.imap_from_filter}")'
        conn = self.imap_factory()
        try:
            conn.login(self.s.imap_user, self.s.imap_password)
            conn.select(self.s.imap_folder, readonly=True)
            typ, data = conn.search(None, crit)
            if typ != "OK":
                raise RuntimeError(f"IMAP search failed: {typ}")
            msgs = []
            for num in data[0].split():
                typ, parts = conn.fetch(num, "(BODY.PEEK[])")
                if typ == "OK" and parts and isinstance(parts[0], tuple):
                    msgs.append(email.message_from_bytes(parts[0][1]))
            return msgs
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    async def run_once(self) -> int:
        msgs = await asyncio.to_thread(self._fetch)
        sent = 0
        for msg in msgs:
            mid = (msg.get("Message-ID") or f"{msg.get('Date')}|{msg.get('Subject')}").strip()
            if self.store.email_seen(mid):
                continue
            if _age_minutes(msg) > self.s.imap_max_age_min:
                self.store.mark_email(mid, alerted=False)
                continue
            text = format_email_alert(msg)
            log.warning("forwarding email alert: %s", decode_subject(msg))
            results = await self.notifier.send(decode_subject(msg) or "Deliverect alert", text)
            if results and not any(results.values()):
                log.error("all channels failed; will retry next pass")
                continue  # not marked -> retried
            self.store.mark_email(mid, alerted=True)
            sent += 1
        return sent

    async def loop(self):
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("email watcher error")
            await asyncio.sleep(self.s.imap_poll_s)


def main():
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings()
    if not s.imap_host:
        sys.exit("IMAP_HOST is not set")
    w = EmailWatcher(s, Store(s.db_path), Notifier(s))
    if "--once" in sys.argv:
        print(f"forwarded {asyncio.run(w.run_once())} alert(s)")
    else:
        asyncio.run(w.loop())


if __name__ == "__main__":
    main()
