"""SQLite order tracking with per-order alert de-duplication."""
import sqlite3
import threading
import time


class Store:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                source TEXT NOT NULL, order_id TEXT NOT NULL,
                display_id TEXT, channel TEXT, store TEXT, status TEXT,
                received_at REAL, acked_at REAL, alerted_at REAL, alert_reason TEXT,
                PRIMARY KEY (source, order_id))"""
        )
        self._db.commit()

    def upsert(self, source, order_id, *, display_id=None, channel=None, store=None, status=None):
        with self._lock:
            self._db.execute(
                """INSERT INTO orders (source, order_id, display_id, channel, store, status, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, order_id) DO UPDATE SET
                     display_id = COALESCE(excluded.display_id, display_id),
                     channel = COALESCE(excluded.channel, channel),
                     store = COALESCE(excluded.store, store),
                     status = COALESCE(excluded.status, status)""",
                (source, order_id, display_id, channel, store, status, time.time()),
            )
            self._db.commit()

    def mark_acked(self, source, order_id):
        with self._lock:
            self._db.execute(
                "UPDATE orders SET acked_at = COALESCE(acked_at, ?) WHERE source=? AND order_id=?",
                (time.time(), source, order_id),
            )
            self._db.commit()

    def claim_alert(self, source, order_id, reason) -> bool:
        """Atomically mark an order as alerted. Returns False if it was already alerted."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE orders SET alerted_at=?, alert_reason=? WHERE source=? AND order_id=? AND alerted_at IS NULL",
                (time.time(), reason, source, order_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def get(self, source, order_id):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM orders WHERE source=? AND order_id=?", (source, order_id)
            ).fetchone()
            return dict(row) if row else None

    def overdue(self, timeout_s: float):
        cutoff = time.time() - timeout_s
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM orders WHERE acked_at IS NULL AND alerted_at IS NULL AND received_at < ?",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_alerts(self, limit=50):
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM orders WHERE alerted_at IS NOT NULL ORDER BY alerted_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
