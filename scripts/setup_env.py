"""Interactive .env setup. Secrets are typed hidden (getpass) and only written to .env.

    python scripts/setup_env.py          # build .env (Telegram + Gmail IMAP), verify each one
    python scripts/setup_env.py --test   # call /test-alert on the running service using INGEST_TOKEN from .env

Nothing secret is printed. .env is created with permissions 600 and is git-ignored.
"""
import getpass
import imaplib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def tg(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(url, data=data, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # Telegram returns JSON errors; never echo the URL (contains token)
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "description": f"network error: {e.reason}"}


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(values: dict):
    """Fill .env.example's template so comments/defaults stay; unquoted values (works with `. ./.env` and docker)."""
    lines, seen = [], set()
    for line in EXAMPLE.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in values:
            lines.append(f"{m.group(1)}={values[m.group(1)]}")
            seen.add(m.group(1))
        else:
            lines.append(line)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.chmod(ENV, 0o600)
    except OSError:
        pass


def ask_secret(prompt: str) -> str:
    v = getpass.getpass(prompt + " (hidden): ").strip()
    if not v:
        sys.exit("empty value, aborting")
    return v


def setup_telegram(env: dict):
    print("\n== Telegram ==")
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    if token and input("Keep existing bot token from .env? [Y/n] ").strip().lower() == "n":
        token = ""
    while True:
        token = token or ask_secret("Paste bot token from @BotFather")
        me = tg(token, "getMe")
        if me.get("ok"):
            print(f"OK: bot @{me['result']['username']}")
            break
        print(f"Token rejected: {me.get('description')}")
        token = ""
    tg(token, "deleteWebhook")  # getUpdates doesn't work while a webhook is set

    print("Add the bot to your alerts group, then send any message in the group (e.g. /start@yourbot).")
    print("Waiting for group messages... (Ctrl+C to stop)")
    chats = {}
    try:
        for _ in range(60):
            upd = tg(token, "getUpdates", timeout=10, allowed_updates=json.dumps(["message", "my_chat_member"]))
            for u in upd.get("result", []):
                msg = u.get("message") or u.get("my_chat_member") or {}
                c = msg.get("chat") or {}
                if c.get("type") in ("group", "supergroup", "channel"):
                    chats[str(c["id"])] = c.get("title", "")
            if chats:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    if not chats:
        sys.exit("No group seen. Tip: if the bot can't see messages, send /start@<botname> or disable privacy in @BotFather.")
    ids = list(chats)
    for i, cid in enumerate(ids, 1):
        print(f"  {i}. {chats[cid]}  chat_id={cid}")
    pick = input("Use which? [1] ").strip() or "1"
    cid = ids[int(pick) - 1]
    if cid.startswith("-") and not cid.startswith("-100"):
        print("Note: basic group. If you later make it a supergroup the id changes to -100...; rerun this script.")
    r = tg(token, "sendMessage", chat_id=cid, text="✅ Failed Order Alerts: bot connected to this group")
    print("Test message sent." if r.get("ok") else f"sendMessage failed: {r.get('description')}")
    env["TELEGRAM_BOT_TOKEN"] = token
    env["TELEGRAM_CHAT_IDS"] = cid


def setup_imap(env: dict):
    print("\n== Gmail inbox (Deliverect alert emails) ==")
    if input("Configure Gmail IMAP watcher now? [Y/n] ").strip().lower() == "n":
        return
    user = input(f"Gmail address [{env.get('IMAP_USER', '')}]: ").strip() or env.get("IMAP_USER", "")
    while True:
        # Gmail shows App Passwords as 4 groups of 4; spaces would break `. ./.env`, Gmail accepts it without them
        pw = re.sub(r"\s+", "", ask_secret("Paste the 16-character Google App Password"))
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
            m.login(user, pw)
            m.select("INBOX", readonly=True)
            m.logout()
            print("OK: IMAP login works")
            break
        except imaplib.IMAP4.error:
            print("Login failed: check the address, that 2-Step Verification is on, and that you pasted an App Password (not the account password).")
        except OSError as e:
            sys.exit(f"Cannot reach imap.gmail.com: {e}")
    env.update(IMAP_HOST="imap.gmail.com", IMAP_PORT="993", IMAP_USER=user, IMAP_PASSWORD=pw)


def main():
    env = {**read_env(EXAMPLE), **read_env(ENV)}
    setup_telegram(env)
    setup_imap(env)
    if env.get("INGEST_TOKEN", "change-me") in ("", "change-me"):
        env["INGEST_TOKEN"] = secrets.token_urlsafe(24)
    if env.get("DB_PATH", "orders.db") == "orders.db" and Path("/data").is_dir():
        env["DB_PATH"] = "/data/orders.db"
    write_env(env)
    print(f"\nWrote {ENV} (mode 600, git-ignored). Next:\n  python scripts/setup_env.py --test   # after starting the service")


def test_alert():
    env = read_env(ENV)
    port = os.getenv("PORT", "8000")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/test-alert", method="POST",
        headers={"X-Alert-Token": env.get("INGEST_TOKEN", "")},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"/test-alert -> HTTP {e.code} (401 = token mismatch: restart the service after editing .env)")
    except urllib.error.URLError as e:
        sys.exit(f"Service not reachable on port {port}: {e.reason}")
    print(json.dumps(res, ensure_ascii=False))
    ch = res.get("channels", {})
    if not ch:
        sys.exit("No channels enabled - .env not loaded into the service?")
    sys.exit(0 if all(ch.values()) else 1)


if __name__ == "__main__":
    test_alert() if "--test" in sys.argv else main()
