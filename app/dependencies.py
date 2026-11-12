"""FastAPI 依赖：JWT 解析 → 当前用户；RBAC 角色校验；租户上下文注入。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .database import get_db
from .errors import Forbidden, Unauthorized
from .logging_config import tenant_id_var
from .models import Member, User
from .security import decode_access_token

# 角色层级：数值越大权限越高
ROLE_LEVEL = {
    'PLATFORM_ADMIN': 100,
    'TENANT_ADMIN': 80,
    'STORE_MANAGER': 60,
    'COACH': 40,
}


@dataclass
class AuthUser:
    id: str
    tenant_id: str
    role: str
    store_id: str
    name: str


def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise Unauthorized('缺少访问令牌')
    return authorization[7:].strip()


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> AuthUser:
    token = _extract_bearer(authorization)
    payload = decode_access_token(token)
    user_id = payload.get('sub', '')
    tenant_id = payload.get('tid', '')
    user = db.get(User, user_id)
    if user is None or user.status != 'ACTIVE':
        raise Unauthorized('账号不存在或已停用')
    if user.tenant_id != tenant_id:
        raise Unauthorized('令牌租户与账号不一致')
    tenant_id_var.set(tenant_id)
    # 角色以数据库为准（BUG-009）：令牌里的 role 是签发时的快照，账号被降级后
    # 旧令牌在有效期内不应继续持有高权限；store_id 同理取库中当前值。
    return AuthUser(
        id=user.id,
        tenant_id=tenant_id,
        role=user.role or payload.get('role', ''),
        store_id=user.store_id or '',
        name=user.real_name or user.username,
    )


def require_min_role(min_role: str):
    """角色下限依赖：如 require_min_role('TENANT_ADMIN') 允许馆主及以上。"""
    def _dep(auth: AuthUser = Depends(get_current_user)) -> AuthUser:
        if ROLE_LEVEL.get(auth.role, 0) < ROLE_LEVEL.get(min_role, 0):
            raise Forbidden('当前角色无权执行该操作')
        return auth
    return _dep


def require_roles(*roles: str):
    """精确角色集合依赖。"""
    allowed = set(roles)

    def _dep(auth: AuthUser = Depends(get_current_user)) -> AuthUser:
        if auth.role not in allowed:
            raise Forbidden('当前角色无权执行该操作')
        return auth
    return _dep


def get_current_member(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Member:
    """会员端 JWT 认证：role 必须为 MEMBER，校验会员存在且租户一致。"""
    token = _extract_bearer(authorization)
    payload = decode_access_token(token)
    if payload.get('role') != 'MEMBER':
        raise Unauthorized('会员令牌无效')
    member_id = payload.get('sub', '')
    tenant_id = payload.get('tid', '')
    member = db.get(Member, member_id)
    if member is None or member.tenant_id != tenant_id:
        raise Unauthorized('会员不存在或已失效')
    tenant_id_var.set(tenant_id)
    return member
