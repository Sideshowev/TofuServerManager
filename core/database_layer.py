"""
Tofu Server Manager - Database Layer

SQLite-based persistent storage for:
- Server metrics history (CPU, RAM, players, etc.)
- Audit logs (who did what, when)
- Settings key-value store
- Plugin state
- Backup history

All access is thread-safe and uses WAL mode for concurrent reads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class DatabaseLayer:
    """Thread-safe SQLite database layer for Tofu."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit mode
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS server_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            cpu_percent REAL DEFAULT 0,
            memory_mb REAL DEFAULT 0,
            player_count INTEGER DEFAULT 0,
            max_players INTEGER DEFAULT 0,
            uptime_seconds REAL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_metrics_server_time
        ON server_metrics(server_id, timestamp);

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            user_id TEXT DEFAULT 'system',
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            details TEXT DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_audit_time
        ON audit_log(timestamp);

        CREATE INDEX IF NOT EXISTS idx_audit_target
        ON audit_log(target_type, target_id);

        CREATE TABLE IF NOT EXISTS plugin_state (
            plugin_id TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT '{}',
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backup_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            backup_path TEXT NOT NULL,
            timestamp REAL NOT NULL,
            file_count INTEGER DEFAULT 0,
            size_bytes INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_backup_server
        ON backup_history(server_id, timestamp);
        """)

    # -- Settings ----------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return default
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return row[0]

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            conn = self._get_conn()
            serialized = json.dumps(value) if not isinstance(value, str) else value
            conn.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
                (key, serialized, time.time()),
            )

    def get_all_settings(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            result = {}
            for key, value in rows:
                try:
                    result[key] = json.loads(value)
                except json.JSONDecodeError:
                    result[key] = value
            return result

    # -- Metrics -----------------------------------------------------------

    def record_metrics(
        self,
        server_id: str,
        cpu_percent: float = 0.0,
        memory_mb: float = 0.0,
        player_count: int = 0,
        max_players: int = 0,
        uptime_seconds: float = 0.0,
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO server_metrics
                   (server_id, timestamp, cpu_percent, memory_mb,
                    player_count, max_players, uptime_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    server_id, time.time(), cpu_percent, memory_mb,
                    player_count, max_players, uptime_seconds,
                ),
            )

    def get_metrics_history(
        self,
        server_id: str,
        since: Optional[float] = None,
        limit: int = 1000,
    ):
        with self._lock:
            conn = self._get_conn()
            if since is not None:
                rows = conn.execute(
                    """SELECT timestamp, cpu_percent, memory_mb, player_count,
                              max_players, uptime_seconds
                       FROM server_metrics
                       WHERE server_id = ? AND timestamp >= ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (server_id, since, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT timestamp, cpu_percent, memory_mb, player_count,
                              max_players, uptime_seconds
                       FROM server_metrics
                       WHERE server_id = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (server_id, limit),
                ).fetchall()
            return [
                {
                    "timestamp": r[0], "cpu_percent": r[1], "memory_mb": r[2],
                    "player_count": r[3], "max_players": r[4],
                    "uptime_seconds": r[5],
                }
                for r in rows
            ]

    def get_metrics_series(self, server_id: str, since: float, bucket_seconds: int,
                           until: Optional[float] = None) -> List[Dict[str, Any]]:
        """Averages per time bucket (oldest first): ts, cpu, ram (MB), players (max).
        Players is None for buckets where the game didn't report players."""
        until = until or time.time()
        b = max(1, int(bucket_seconds))
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT CAST(timestamp / ? AS INTEGER) AS bk, AVG(cpu_percent), AVG(memory_mb),
                          MAX(player_count), COUNT(*)
                   FROM server_metrics
                   WHERE server_id = ? AND timestamp >= ? AND timestamp <= ?
                   GROUP BY bk ORDER BY bk""",
                (b, server_id, since, until),
            ).fetchall()
        return [{"ts": (r[0] * b) + b / 2, "cpu": round(r[1] or 0, 1), "ram": round(r[2] or 0),
                 "players": r[3], "n": r[4]} for r in rows]

    def audit(
        self,
        action: str,
        target_type: str,
        target_id: str,
        user_id: str = "system",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO audit_log
                   (timestamp, user_id, action, target_type, target_id, details)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    time.time(), user_id, action, target_type, target_id,
                    json.dumps(details or {}),
                ),
            )

    def get_audit_log(
        self,
        limit: int = 100,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            if target_type and target_id:
                rows = conn.execute(
                    """SELECT timestamp, user_id, action, target_type, target_id, details
                       FROM audit_log
                       WHERE target_type = ? AND target_id = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (target_type, target_id, limit),
                ).fetchall()
            elif target_type:
                rows = conn.execute(
                    """SELECT timestamp, user_id, action, target_type, target_id, details
                       FROM audit_log
                       WHERE target_type = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (target_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT timestamp, user_id, action, target_type, target_id, details
                       FROM audit_log
                       ORDER BY timestamp DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [
                {
                    "timestamp": r[0], "user_id": r[1], "action": r[2],
                    "target_type": r[3], "target_id": r[4], "details": json.loads(r[5]),
                }
                for r in rows
            ]

    def get_plugin_state(self, plugin_id: str) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT state FROM plugin_state WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
            if row is None:
                return {}
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return {}

    def set_plugin_state(self, plugin_id: str, state: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO plugin_state (plugin_id, state, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(plugin_id) DO UPDATE SET
                   state = excluded.state, updated_at = excluded.updated_at""",
                (plugin_id, json.dumps(state), time.time()),
            )

    def record_backup(
        self,
        server_id: str,
        backup_path: str,
        file_count: int = 0,
        size_bytes: int = 0,
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO backup_history
                   (server_id, backup_path, timestamp, file_count, size_bytes)
                   VALUES (?, ?, ?, ?, ?)""",
                (server_id, backup_path, time.time(), file_count, size_bytes),
            )

    def list_backups(self, server_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT backup_path, timestamp, file_count, size_bytes
                   FROM backup_history
                   WHERE server_id = ?
                   ORDER BY timestamp DESC""",
                (server_id,),
            ).fetchall()
            return [
                {
                    "path": r[0], "timestamp": r[1],
                    "file_count": r[2], "size_bytes": r[3],
                }
                for r in rows
            ]

    def cleanup_old_metrics(self, older_than_seconds: float = 86400 * 7) -> int:
        with self._lock:
            conn = self._get_conn()
            cutoff = time.time() - older_than_seconds
            cur = conn.execute(
                "DELETE FROM server_metrics WHERE timestamp < ?", (cutoff,)
            )
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
