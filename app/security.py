"""密码哈希、JWT 访问令牌与服务端刷新令牌。令牌绝不入 localStorage / URL。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional, Tuple

import jwt

from .config import settings
from .errors import Unauthorized

_PBKDF2_ROUNDS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, _PBKDF2_ROUNDS)
    return 'pbkdf2$%d$%s$%s' % (_PBKDF2_ROUNDS, salt.hex(), dk.hex())


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_hex, dk_hex = encoded.split('$')
        if algo != 'pbkdf2':
            return False
        dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, tenant_id: str, role: str, store_ids: list) -> str:
    now = int(time.time())
    payload = {
        'sub': user_id,
        'tid': tenant_id,
        'role': role,
        'sids': ','.join(store_ids),
        'type': 'access',
        'iat': now,
        'exp': now + settings.ACCESS_TOKEN_MINUTES * 60,
        'jti': secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise Unauthorized('访问令牌无效或已过期') from exc
    if payload.get('type') != 'access':
        raise Unauthorized('令牌类型不正确')
    return payload


def new_refresh_token() -> Tuple[str, str]:
    """返回 (明文令牌, 服务端存储的哈希)。明文只在响应中出现一次。"""
    raw = secrets.token_urlsafe(48)
    return raw, hashlib.sha256(raw.encode('utf-8')).hexdigest()


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def mask_phone(phone: str) -> str:
    """手机号展示脱敏：中间四位掩码。"""
    p = (phone or '').strip()
    return p[:3] + '****' + p[-4:] if len(p) >= 7 else '***'


def phone_hash(phone: str) -> str:
    """手机号检索键：加盐哈希，不存明文。"""
    salt = settings.JWT_SECRET.encode('utf-8')
    return hmac.new(salt, (phone or '').strip().encode('utf-8'), hashlib.sha256).hexdigest()


def random_id() -> str:
    import uuid
    return str(uuid.uuid4())


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a or '', b or '')
