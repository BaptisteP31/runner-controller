import json
import sqlite3
import threading
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self._init_db()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _init_db(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runners (
                    id TEXT PRIMARY KEY, vmid INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL, ipv4 TEXT, created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, memory_mb INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                    action TEXT NOT NULL, runner_id TEXT, vmid INTEGER,
                    result TEXT NOT NULL, upid TEXT, details TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS actions_runner_time ON actions(runner_id, occurred_at);
                CREATE INDEX IF NOT EXISTS runners_expiry ON runners(expires_at);
            """)

    def reserve(self, runner, vmid, expires_at, memory_mb, max_runners):
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT COUNT(*) FROM runners").fetchone()[0]
            if active >= max_runners:
                db.rollback()
                raise RuntimeError("runner capacity reached")
            db.execute("INSERT INTO runners VALUES (?, ?, 'creating', NULL, ?, ?, ?)",
                       (runner, vmid, now(), expires_at, memory_mb))
            db.commit()

    def reserve_available(self, runner, candidates, expires_at, memory_mb, max_runners):
        """Atomically enforce capacity and reserve the first locally free VMID."""
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT vmid FROM runners").fetchall()
            reserved = {row[0] for row in rows}
            if len(reserved) >= max_runners:
                db.rollback()
                raise RuntimeError("runner capacity reached")
            vmid = next((candidate for candidate in candidates if candidate not in reserved), None)
            if vmid is None:
                db.rollback()
                return None
            db.execute("INSERT INTO runners VALUES (?, ?, 'creating', NULL, ?, ?, ?)",
                       (runner, vmid, now(), expires_at, memory_mb))
            db.commit()
            return vmid

    def list_runners(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM runners ORDER BY created_at")]

    def get_runner(self, runner):
        with self._connect() as db:
            row = db.execute("SELECT * FROM runners WHERE id=?", (runner,)).fetchone()
            return dict(row) if row else None

    def update_runner(self, runner, **fields):
        if not fields:
            return
        allowed = {"status", "ipv4", "expires_at"}
        if not fields.keys() <= allowed:
            raise ValueError("unsupported runner field")
        assignments = ",".join(f"{key}=?" for key in fields)
        with self._connect() as db:
            db.execute(f"UPDATE runners SET {assignments} WHERE id=?", (*fields.values(), runner))

    def remove_runner(self, runner):
        with self._connect() as db:
            db.execute("DELETE FROM runners WHERE id=?", (runner,))

    def expired(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM runners WHERE expires_at <= ? ORDER BY expires_at", (now(),))]

    def audit(self, action, result, runner_id=None, vmid=None, upid=None, details=None):
        # Details are caller-supplied safe metadata only; never pass raw API payloads or exceptions.
        with self._connect() as db:
            db.execute("INSERT INTO actions(occurred_at,action,runner_id,vmid,result,upid,details) VALUES(?,?,?,?,?,?,?)",
                       (now(), action, runner_id, vmid, result, upid,
                        json.dumps(details or {}, separators=(",", ":"))))
