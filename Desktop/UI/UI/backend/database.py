"""
SQLite 数据访问层。表结构可迁移到 MySQL：将连接与 SQL 方言抽离即可。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

# 默认数据库文件与 backend 目录同级下的 data 目录
_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "data" / "app.db"

SCHEMA_VERSION = 2


def _ensure_parent() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    _ensure_parent()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_session() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [str(r[1]) for r in cur.fetchall()]


def _needs_schema_reset(conn: sqlite3.Connection) -> bool:
    row = fetch_one(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'",
        (),
    )
    if not row:
        return False
    cols = _table_columns(conn, "users")
    if "phone" in cols or "username" not in cols:
        return True
    meta = fetch_one(conn, "SELECT value FROM meta WHERE key = ?", ("schema_version",))
    if not meta:
        return True
    try:
        return int(str(meta["value"])) < SCHEMA_VERSION
    except ValueError:
        return True


def _drop_all_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS notifications;
        DROP TABLE IF EXISTS tasks;
        DROP TABLE IF EXISTS sessions;
        DROP TABLE IF EXISTS users;
        DROP TABLE IF EXISTS meta;
        """
    )


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            door_plate TEXT NOT NULL,
            match_key TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_match ON tasks(match_key);

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            task_id TEXT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            read_flag INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )


def init_db() -> None:
    """初始化表结构（幂等）；旧版 phone 账号体系会整库清空并重建。"""
    _ensure_parent()
    with db_session() as conn:
        if _needs_schema_reset(conn):
            _drop_all_tables(conn)
        _create_schema(conn)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def fetch_one(conn: sqlite3.Connection, sql: str, params: Iterable[Any]) -> Optional[sqlite3.Row]:
    cur = conn.execute(sql, tuple(params))
    return cur.fetchone()


def fetch_all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, tuple(params))
    return [row_to_dict(r) for r in cur.fetchall()]
