"""SQLite database layer for AgentPod user registry and usage logging."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, date, datetime


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(
            """\
            CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                api_key     TEXT UNIQUE NOT NULL,
                cwd_path    TEXT NOT NULL,
                config      TEXT NOT NULL DEFAULT '{}',
                is_active   BOOLEAN NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key);

            CREATE TABLE IF NOT EXISTS usage_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                session_id      TEXT NOT NULL,
                model           TEXT NOT NULL,
                turns           INTEGER NOT NULL DEFAULT 0,
                input_tokens    INTEGER NOT NULL DEFAULT 0,
                output_tokens   INTEGER NOT NULL DEFAULT 0,
                cached_tokens   INTEGER NOT NULL DEFAULT 0,
                cost_amount     REAL NOT NULL DEFAULT 0.0,
                duration_ms     INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_usage_logs_user_date
                ON usage_logs(user_id, created_at);
            """
        )

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    def create_user(self, user_id: str, cwd_path: str, config: str = "{}") -> str:
        api_key = "sk-" + secrets.token_hex(16)
        now = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO users (id, api_key, cwd_path, config, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (user_id, api_key, cwd_path, config, now, now),
        )
        conn.commit()
        return api_key

    def get_user_by_api_key(self, api_key: str) -> dict | None:
        row = self._get_conn().execute(
            "SELECT * FROM users WHERE api_key = ?", (api_key,)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict | None:
        row = self._get_conn().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        rows = self._get_conn().execute(
            "SELECT * FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_config(self, user_id: str, config_json: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE users SET config = ?, updated_at = ? WHERE id = ?",
            (config_json, now, user_id),
        )
        self._get_conn().commit()

    def disable_user(self, user_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE users SET is_active = 0, updated_at = ? WHERE id = ?",
            (now, user_id),
        )
        self._get_conn().commit()

    def enable_user(self, user_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE users SET is_active = 1, updated_at = ? WHERE id = ?",
            (now, user_id),
        )
        self._get_conn().commit()

    def reset_api_key(self, user_id: str) -> str:
        new_key = "sk-" + secrets.token_hex(16)
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE users SET api_key = ?, updated_at = ? WHERE id = ?",
            (new_key, now, user_id),
        )
        self._get_conn().commit()
        return new_key

    # ------------------------------------------------------------------
    # Usage logging
    # ------------------------------------------------------------------

    def log_usage(
        self,
        user_id: str,
        session_id: str,
        model: str,
        turns: int,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cost_amount: float,
        duration_ms: int,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "INSERT INTO usage_logs "
            "(user_id, session_id, model, turns, input_tokens, output_tokens, "
            "cached_tokens, cost_amount, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, session_id, model, turns,
                input_tokens, output_tokens, cached_tokens,
                cost_amount, duration_ms, now,
            ),
        )
        self._get_conn().commit()

    def get_daily_cost(self, user_id: str, target_date: date | None = None) -> float:
        if target_date is None:
            target_date = date.today()
        date_prefix = target_date.isoformat()
        row = self._get_conn().execute(
            "SELECT COALESCE(SUM(cost_amount), 0.0) AS total "
            "FROM usage_logs "
            "WHERE user_id = ? AND created_at LIKE ?",
            (user_id, date_prefix + "%"),
        ).fetchone()
        return float(row["total"])

    def get_usage(
        self,
        user_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM usage_logs WHERE user_id = ?"
        params: list[str] = [user_id]
        if from_date:
            query += " AND created_at >= ?"
            params.append(from_date)
        if to_date:
            query += " AND created_at < ?"
            params.append(to_date)
        query += " ORDER BY created_at"
        rows = self._get_conn().execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def count_users(self) -> int:
        row = self._get_conn().execute("SELECT COUNT(*) AS total FROM users").fetchone()
        return row["total"]

    def get_daily_stats(self, target_date: date | None = None) -> dict:
        if target_date is None:
            target_date = date.today()
        date_prefix = target_date.isoformat()
        row = self._get_conn().execute(
            "SELECT "
            "  COUNT(*) AS total_queries, "
            "  COALESCE(SUM(input_tokens), 0) AS total_input_tokens, "
            "  COALESCE(SUM(output_tokens), 0) AS total_output_tokens, "
            "  COALESCE(SUM(cost_amount), 0.0) AS total_cost, "
            "  COUNT(DISTINCT user_id) AS active_users "
            "FROM usage_logs WHERE created_at LIKE ?",
            (date_prefix + "%",),
        ).fetchone()
        return dict(row)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
