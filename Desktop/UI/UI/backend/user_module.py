"""
用户模块：注册、登录、密码校验。
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Optional

from database import db_session, fetch_one


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return dk.hex()


def normalize_username(username: str) -> str:
    return username.strip()


def register_user(username: str, login_password: str) -> tuple[bool, str, Optional[int]]:
    """
    注册：用户名 + 登录密码（登录与到站取件共用同一密码）。
    返回 (成功, 消息, user_id)。
    """
    username = normalize_username(username)
    if len(username) < 2:
        return False, "用户名至少 2 个字符", None
    if len(username) > 32:
        return False, "用户名最多 32 个字符", None
    if len(login_password) < 4:
        return False, "登录密码至少 4 位", None

    salt = secrets.token_hex(16)
    pw_hash = _hash_password(login_password, salt)
    try:
        with db_session() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, password_salt) VALUES (?,?,?)",
                (username, pw_hash, salt),
            )
            row = fetch_one(conn, "SELECT id FROM users WHERE username = ?", (username,))
            uid = int(row["id"]) if row else None
        return True, "注册成功", uid
    except sqlite3.IntegrityError:
        return False, "用户名已存在", None


def verify_login(username: str, login_password: str) -> tuple[bool, str, Optional[int]]:
    """校验登录密码。"""
    username = normalize_username(username)
    with db_session() as conn:
        row = fetch_one(
            conn,
            "SELECT id, password_hash, password_salt FROM users WHERE username = ?",
            (username,),
        )
        if not row:
            return False, "用户不存在", None
        h = _hash_password(login_password, str(row["password_salt"]))
        if h != row["password_hash"]:
            return False, "登录密码错误", None
        return True, "登录成功", int(row["id"])


def verify_login_password(user_id: int, login_password: str) -> bool:
    """到站取件：校验登录密码。"""
    with db_session() as conn:
        row = fetch_one(
            conn,
            "SELECT password_hash, password_salt FROM users WHERE id = ?",
            (user_id,),
        )
        if not row:
            return False
        h = _hash_password(login_password, str(row["password_salt"]))
        return h == row["password_hash"]


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with db_session() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user_id)
        )
    return token


def resolve_session(token: str) -> Optional[int]:
    if not token:
        return None
    with db_session() as conn:
        row = fetch_one(
            conn, "SELECT user_id FROM sessions WHERE token = ?", (token,)
        )
        return int(row["user_id"]) if row else None


def delete_session(token: str) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
