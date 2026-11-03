"""All settings come from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field


def _set(name: str, default: str) -> set[str]:
    return {s.strip().upper() for s in os.getenv(name, default).split(",") if s.strip()}


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "orders.db"))
    ingest_token: str = field(default_factory=lambda: os.getenv("INGEST_TOKEN", ""))
    # Order received but never acknowledged by the store/POS within N minutes -> alert.
    ack_timeout_min: float = field(default_factory=lambda: float(os.getenv("ACK_TIMEOUT_MINUTES", "5")))
    watchdog_interval_s: float = field(default_factory=lambda: float(os.getenv("WATCHDOG_INTERVAL_SECONDS", "30")))
    alert_on_cancel: bool = field(default_factory=lambda: os.getenv("ALERT_ON_CANCEL", "false").lower() == "true")

    # Telegram
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_ids: list[str] = field(
        default_factory=lambda: [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    )
    # Email (SMTP)
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    email_from: str = field(default_factory=lambda: os.getenv("EMAIL_FROM", ""))
    email_to: list[str] = field(
        default_factory=lambda: [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
    )
    # Slack / Teams / Google Chat / any incoming webhook that accepts {"text": "..."}
    chat_webhook_urls: list[str] = field(
        default_factory=lambda: [u.strip() for u in os.getenv("CHAT_WEBHOOK_URLS", "").split(",") if u.strip()]
    )

    # Email inbox watcher: reads Deliverect alert emails and forwards them (Telegram etc.)
    imap_host: str = field(default_factory=lambda: os.getenv("IMAP_HOST", ""))
    imap_port: int = field(default_factory=lambda: int(os.getenv("IMAP_PORT", "993")))
    imap_user: str = field(default_factory=lambda: os.getenv("IMAP_USER", ""))
    imap_password: str = field(default_factory=lambda: os.getenv("IMAP_PASSWORD", ""))
    imap_folder: str = field(default_factory=lambda: os.getenv("IMAP_FOLDER", "INBOX"))
    imap_from_filter: str = field(default_factory=lambda: os.getenv("IMAP_FROM_FILTER", "deliverect"))
    imap_poll_s: float = field(default_factory=lambda: float(os.getenv("IMAP_POLL_SECONDS", "60")))
    # Emails older than this are recorded but not alerted (avoids a flood on first start).
    imap_max_age_min: float = field(default_factory=lambda: float(os.getenv("IMAP_MAX_AGE_MINUTES", "60")))

    # Deliverect numeric status codes. VERIFY against your Deliverect account docs.
    deliverect_received: set[str] = field(default_factory=lambda: _set("DELIVERECT_RECEIVED_STATUSES", "1,4"))
    deliverect_acked: set[str] = field(
        default_factory=lambda: _set("DELIVERECT_ACK_STATUSES", "2,20,40,50,60,70,80,85,86,90,95")
    )
    deliverect_failed: set[str] = field(default_factory=lambda: _set("DELIVERECT_FAILED_STATUSES", "120,121,122,124"))
    deliverect_cancelled: set[str] = field(default_factory=lambda: _set("DELIVERECT_CANCEL_STATUSES", "100,110"))

    # UrbanPiper order states (case-insensitive).
    urbanpiper_received: set[str] = field(default_factory=lambda: _set("URBANPIPER_RECEIVED_STATES", "PLACED"))
    urbanpiper_acked: set[str] = field(
        default_factory=lambda: _set("URBANPIPER_ACK_STATES", "ACKNOWLEDGED,FOOD READY,DISPATCHED,COMPLETED")
    )
    urbanpiper_failed: set[str] = field(default_factory=lambda: _set("URBANPIPER_FAILED_STATES", "FAILED"))
    urbanpiper_cancelled: set[str] = field(default_factory=lambda: _set("URBANPIPER_CANCEL_STATES", "CANCELLED"))
