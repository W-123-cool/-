"""操作员 PIN 鉴权（P1a）。"""
from __future__ import annotations

import secrets
import time
from typing import Optional

from patrol_mode.config import OPERATOR_PIN, TOKEN_TTL_SEC

_tokens: dict[str, float] = {}


def login(pin: str) -> tuple[bool, str, Optional[str]]:
    if pin.strip() != OPERATOR_PIN:
        return False, "PIN 错误", None
    token = secrets.token_urlsafe(24)
    _tokens[token] = time.time() + TOKEN_TTL_SEC
    return True, "登录成功", token


def verify_token(token: str) -> bool:
    if not token:
        return False
    exp = _tokens.get(token.strip())
    if exp is None or exp < time.time():
        _tokens.pop(token.strip(), None)
        return False
    return True
