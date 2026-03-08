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
                budget      REAL NOT NULL DEFAULT 0.0,
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

            CREATE TABLE IF NOT EXISTS cron_tasks (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                task_name       TEXT NOT NULL,
                description     TEXT NOT NULL DEFAULT '',
                schedule        TEXT NOT NULL,
                timezone        TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                enabled         BOOLEAN NOT NULL DEFAULT 1,
                deleted         BOOLEAN NOT NULL DEFAULT 0,
                timeout         INTEGER NOT NULL DEFAULT 1200,
                max_turns       INTEGER NOT NULL DEFAULT 100,
                model           TEXT NOT NULL DEFAULT '',
                content_hash     TEXT NOT NULL DEFAULT '',
                last_synced_at  TEXT NOT NULL,
                next_run_at     TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS cron_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                task_name       TEXT NOT NULL,
                session_id      TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'running',
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                error_message   TEXT,
                cost_amount     REAL NOT NULL DEFAULT 0.0,
                input_tokens    INTEGER NOT NULL DEFAULT 0,
                output_tokens   INTEGER NOT NULL DEFAULT 0,
                turns           INTEGER NOT NULL DEFAULT 0,
                duration_ms     INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (task_id) REFERENCES cron_tasks(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS webhook_dead_letters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                payload     TEXT NOT NULL,
                attempts    INTEGER NOT NULL DEFAULT 0,
                last_error  TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_webhook_dl_created
                ON webhook_dead_letters(created_at);
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
            "INSERT INTO users (id, api_key, cwd_path, config, budget, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0.0, 1, ?, ?)",
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
    # Cron tasks
    # ------------------------------------------------------------------

    def upsert_cron_task(self, task_id, user_id, task_name, description,
                         schedule, timezone, enabled, timeout, max_turns,
                         model, content_hash, next_run_at) -> None:
        """Insert or update a cron task (used by sync)."""
        now = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id, deleted FROM cron_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE cron_tasks SET description=?, schedule=?, timezone=?, enabled=?, "
                "deleted=0, timeout=?, max_turns=?, model=?, content_hash=?, "
                "last_synced_at=?, next_run_at=?, updated_at=? WHERE id=?",
                (description, schedule, timezone, enabled, timeout, max_turns, model,
                 content_hash, now, next_run_at, now, task_id),
            )
        else:
            conn.execute(
                "INSERT INTO cron_tasks (id, user_id, task_name, description, schedule, "
                "timezone, enabled, deleted, timeout, max_turns, model, content_hash, "
                "last_synced_at, next_run_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?)",
                (task_id, user_id, task_name, description, schedule, timezone, enabled,
                 timeout, max_turns, model, content_hash, now, next_run_at, now, now),
            )
        conn.commit()

    def soft_delete_cron_task(self, task_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE cron_tasks SET deleted=1, updated_at=? WHERE id=?", (now, task_id)
        )
        self._get_conn().commit()

    def get_cron_task(self, task_id: str) -> dict | None:
        row = self._get_conn().execute(
            "SELECT * FROM cron_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_cron_tasks(self, user_id: str, include_deleted: bool = False) -> list[dict]:
        if include_deleted:
            rows = self._get_conn().execute(
                "SELECT * FROM cron_tasks WHERE user_id = ? ORDER BY task_name",
                (user_id,),
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT * FROM cron_tasks WHERE user_id = ? AND deleted = 0 "
                "ORDER BY task_name",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_cron_tasks(self, include_deleted: bool = False) -> list[dict]:
        if include_deleted:
            rows = self._get_conn().execute(
                "SELECT * FROM cron_tasks ORDER BY user_id, task_name"
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT * FROM cron_tasks WHERE deleted = 0 "
                "ORDER BY user_id, task_name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_due_cron_tasks(self, now_iso: str) -> list[dict]:
        """Get tasks that are due to run (enabled, not deleted, next_run_at <= now)."""
        rows = self._get_conn().execute(
            "SELECT * FROM cron_tasks WHERE enabled = 1 AND deleted = 0 "
            "AND next_run_at <= ? ORDER BY next_run_at",
            (now_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_cron_next_run(self, task_id: str, next_run_at: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE cron_tasks SET next_run_at = ?, updated_at = ? WHERE id = ?",
            (next_run_at, now, task_id),
        )
        self._get_conn().commit()

    def enable_cron_task(self, task_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE cron_tasks SET enabled = 1, updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        self._get_conn().commit()

    def disable_cron_task(self, task_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE cron_tasks SET enabled = 0, updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        self._get_conn().commit()

    # ------------------------------------------------------------------
    # Cron runs
    # ------------------------------------------------------------------

    def create_cron_run(self, task_id, user_id, task_name, session_id) -> int:
        now = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO cron_runs (task_id, user_id, task_name, session_id, "
            "status, started_at) VALUES (?, ?, ?, ?, 'running', ?)",
            (task_id, user_id, task_name, session_id, now),
        )
        conn.commit()
        return cursor.lastrowid

    def finish_cron_run(self, run_id: int, status: str,
                        error_message: str | None = None,
                        cost_amount: float = 0.0, input_tokens: int = 0,
                        output_tokens: int = 0, turns: int = 0,
                        duration_ms: int = 0) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "UPDATE cron_runs SET status=?, finished_at=?, error_message=?, "
            "cost_amount=?, input_tokens=?, output_tokens=?, turns=?, "
            "duration_ms=? WHERE id=?",
            (status, now, error_message, cost_amount, input_tokens,
             output_tokens, turns, duration_ms, run_id),
        )
        self._get_conn().commit()

    def get_cron_run(self, run_id: int) -> dict | None:
        row = self._get_conn().execute(
            "SELECT * FROM cron_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_cron_runs(self, user_id: str, task_name: str | None = None,
                       limit: int = 50) -> list[dict]:
        if task_name:
            rows = self._get_conn().execute(
                "SELECT * FROM cron_runs WHERE user_id = ? AND task_name = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (user_id, task_name, limit),
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT * FROM cron_runs WHERE user_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_cron_runs(self, user_id: str | None = None,
                           status: str | None = None,
                           limit: int = 50) -> list[dict]:
        query = "SELECT * FROM cron_runs WHERE 1=1"
        params: list = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = self._get_conn().execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def has_running_cron_run(self, task_id: str) -> bool:
        row = self._get_conn().execute(
            "SELECT COUNT(*) AS cnt FROM cron_runs "
            "WHERE task_id = ? AND status = 'running'",
            (task_id,),
        ).fetchone()
        return row["cnt"] > 0

    def get_cron_stats(self) -> dict:
        """Get cron statistics for admin stats endpoint."""
        conn = self._get_conn()
        tasks_row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN enabled=1 AND deleted=0 THEN 1 ELSE 0 END) AS enabled "
            "FROM cron_tasks WHERE deleted=0"
        ).fetchone()
        active_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM cron_runs WHERE status='running'"
        ).fetchone()
        date_prefix = date.today().isoformat()
        today_row = conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(cost_amount), 0.0) AS cost "
            "FROM cron_runs WHERE started_at LIKE ?",
            (date_prefix + "%",),
        ).fetchone()
        return {
            "total_tasks": tasks_row["total"],
            "enabled_tasks": tasks_row["enabled"] or 0,
            "active_runs": active_row["cnt"],
            "runs_today": today_row["runs"],
            "cron_cost_today": today_row["cost"],
        }

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------

    def add_budget(self, user_id: str, amount: float) -> float:
        """Increase user budget, return new balance."""
        now = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        conn.execute(
            "UPDATE users SET budget = budget + ?, updated_at = ? WHERE id = ?",
            (amount, now, user_id),
        )
        conn.commit()
        row = conn.execute("SELECT budget FROM users WHERE id = ?", (user_id,)).fetchone()
        return float(row["budget"])

    def deduct_budget(self, user_id: str, amount: float) -> bool:
        """Atomically deduct budget. Returns False if insufficient balance."""
        now = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE users SET budget = budget - ?, updated_at = ? "
            "WHERE id = ? AND budget >= ?",
            (amount, now, user_id, amount),
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_budget(self, user_id: str) -> float:
        """Get user's current budget."""
        row = self._get_conn().execute(
            "SELECT budget FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return float(row["budget"]) if row else 0.0

    # ------------------------------------------------------------------
    # Webhook dead letters
    # ------------------------------------------------------------------

    def insert_dead_letter(self, event_id: str, event_type: str,
                           payload: str, attempts: int, last_error: str | None) -> None:
        now = datetime.now(UTC).isoformat()
        self._get_conn().execute(
            "INSERT INTO webhook_dead_letters "
            "(event_id, event_type, payload, attempts, last_error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, event_type, payload, attempts, last_error, now),
        )
        self._get_conn().commit()

    def list_dead_letters(self, limit: int = 50) -> list[dict]:
        rows = self._get_conn().execute(
            "SELECT * FROM webhook_dead_letters ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_dead_letter(self, dl_id: int) -> dict | None:
        row = self._get_conn().execute(
            "SELECT * FROM webhook_dead_letters WHERE id = ?", (dl_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_dead_letter(self, dl_id: int) -> None:
        self._get_conn().execute(
            "DELETE FROM webhook_dead_letters WHERE id = ?", (dl_id,)
        )
        self._get_conn().commit()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
