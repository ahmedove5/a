import asyncio
import email.utils
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from app.config import Settings
from app.email_watcher import EmailWatcher, format_email_alert
from app.store import Store


class FakeNotifier:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    async def send(self, subject, body):
        self.sent.append((subject, body))
        return {"telegram": self.ok}


def make_msg(subject, html_body=None, text=None, mid="<1@x>", age_min=1):
    m = EmailMessage()
    m["Subject"], m["From"], m["Message-ID"] = subject, "Deliverect <noreply@deliverect.com>", mid
    m["Date"] = email.utils.format_datetime(datetime.now(timezone.utc) - timedelta(minutes=age_min))
    if text:
        m.set_content(text)
    if html_body:
        if text:
            m.add_alternative(html_body, subtype="html")
        else:
            m.set_content(html_body, subtype="html")
    return m


class FakeIMAP:
    def __init__(self, msgs):
        self.msgs = msgs
        self.calls = []

    def login(self, u, p): self.calls.append("login")
    def select(self, folder, readonly=False): self.calls.append(("select", readonly))
    def search(self, charset, crit):
        self.calls.append(("search", crit))
        return "OK", [" ".join(str(i + 1) for i in range(len(self.msgs))).encode()]
    def fetch(self, num, what):
        assert "PEEK" in what
        return "OK", [(b"x", self.msgs[int(num) - 1].as_bytes())]
    def logout(self): pass


def test_format_html_table_email():
    m = make_msg("Order injection failed - Kunafa Riyadh", html_body="""
      <table><tr><td>Order</td><td>#6638</td></tr>
      <tr><td>Channel</td><td>Keeta</td></tr>
      <tr><td>Location</td><td>Riyadh Olaya</td></tr>
      <tr><td>Reason</td><td>POS timeout</td></tr></table>
      <p>ID 6aea52fa0811e3e27f1a08d9</p>""")
    out = format_email_alert(m)
    assert "ما وصل للفرع" in out
    for s in ("#6638", "Keeta", "Riyadh Olaya", "POS timeout", "6aea52fa0811e3e27f1a08d9"):
        assert s in out


def test_unknown_format_forwards_body():
    m = make_msg("Something happened", text="Free text with no fields, branch is down")
    out = format_email_alert(m)
    assert "Free text with no fields" in out


def run(watcher):
    return asyncio.run(watcher.run_once())


def watcher_for(tmp_path, msgs, notifier):
    s = Settings()
    s.imap_host, s.imap_max_age_min = "imap.test", 60
    imap = FakeIMAP(msgs)
    return EmailWatcher(s, Store(str(tmp_path / "t.db")), notifier, imap_factory=lambda: imap), imap


def test_forwards_once_and_readonly(tmp_path):
    n = FakeNotifier()
    w, imap = watcher_for(tmp_path, [make_msg("Order injection failed", text="Order: 1")], n)
    assert run(w) == 1
    assert run(w) == 0  # dedup
    assert len(n.sent) == 1
    assert ("select", True) in imap.calls
    assert any("FROM \"deliverect\"" in c[1] for c in imap.calls if c[0] == "search")


def test_old_emails_not_alerted(tmp_path):
    n = FakeNotifier()
    w, _ = watcher_for(tmp_path, [make_msg("Order injection failed", text="x", age_min=600)], n)
    assert run(w) == 0 and n.sent == []


def test_retry_when_all_channels_fail(tmp_path):
    n = FakeNotifier(ok=False)
    w, _ = watcher_for(tmp_path, [make_msg("Order injection failed", text="Order: 1")], n)
    assert run(w) == 0
    n.ok = True
    assert run(w) == 1
