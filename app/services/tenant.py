"""租户 / 门店 / 账号服务。账号含教练。"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessRuleError, Conflict, NotFound, ValidationError
from ..models import Store, Tenant, User
from ..repositories.base import write_audit
from ..security import hash_password, mask_phone, random_id

_ROLES = ('PLATFORM_ADMIN', 'TENANT_ADMIN', 'STORE_MANAGER', 'COACH')


# ---------- 门店 ----------
def create_store(db: Session, tenant_id: str, operator: Dict, data: dict) -> Store:
    if not data.get('name'):
        raise ValidationError('门店名称不能为空')
    code = data.get('code') or ('S%03d' % random_id()[-3:].upper())
    exists = db.scalar(select(Store).where(Store.tenant_id == tenant_id, Store.code == code))
    if exists:
        raise Conflict('门店编码已存在')
    store = Store(id=random_id(), tenant_id=tenant_id, code=code, name=data['name'],
                  address=data.get('address', ''), phone_mask=data.get('phone_mask', ''),
                  business_hours=data.get('business_hours', '09:00-21:00'))
    db.add(store)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'store', store.id,
                {'name': store.name, 'code': store.code})
    return store


def list_stores(db: Session, tenant_id: str) -> List[Store]:
    return list(db.scalars(select(Store).where(Store.tenant_id == tenant_id)).all())


# ---------- 账号 ----------
def create_user(db: Session, tenant_id: str, operator: Dict, data: dict) -> User:
    username = (data.get('username') or '').strip()
    role = data.get('role') or 'COACH'
    if not username:
        raise ValidationError('账号名不能为空')
    if role not in _ROLES:
        raise ValidationError('非法角色')
    exists = db.scalar(select(User).where(User.tenant_id == tenant_id, User.username == username))
    if exists:
        raise Conflict('账号名已存在')
    pwd = data.get('password') or '123456'
    user = User(id=random_id(), tenant_id=tenant_id, username=username,
                password_hash=hash_password(pwd), real_name=data.get('real_name', ''),
                role=role, store_id=data.get('store_id', ''))
    db.add(user)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'user', user.id,
                {'username': username, 'role': role})
    return user


def list_users(db: Session, tenant_id: str, role: str = '') -> List[User]:
    stmt = select(User).where(User.tenant_id == tenant_id)
    if role:
        stmt = stmt.where(User.role == role)
    return list(db.scalars(stmt).all())


def reset_password(db: Session, tenant_id: str, operator: Dict, user_id: str, new_password: str) -> User:
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise NotFound('账号不存在')
    if not new_password or len(new_password) < 6:
        raise ValidationError('密码至少 6 位')
    user.password_hash = hash_password(new_password)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'UPDATE', 'user', user.id,
                {'action': 'reset_password'})
    return user


# ---------- 教练 ----------
def list_coaches(db: Session, tenant_id: str) -> List[User]:
    return list(db.scalars(
        select(User).where(User.tenant_id == tenant_id, User.role == 'COACH')).all())


# ---------- 租户（平台级） ----------
def create_tenant(db: Session, operator: Dict, data: dict) -> Tenant:
    code = (data.get('code') or '').strip()
    name = (data.get('name') or '').strip()
    if not name:
        raise ValidationError('品牌名称不能为空')
    if not code:
        code = name[:20]
    exists = db.scalar(select(Tenant).where(Tenant.code == code))
    if exists:
        raise Conflict('品牌编码已存在')
    t = Tenant(id=random_id(), code=code, name=name,
               contact_name=data.get('contact_name', ''),
               contact_phone_mask=mask_phone(data.get('contact_phone', '')))
    db.add(t)
    # 默认创建租户管理员账号
    admin = User(id=random_id(), tenant_id=t.id, username=data.get('admin_username') or 'admin',
                 password_hash=hash_password(data.get('admin_password') or '123456'),
                 real_name='馆主', role='TENANT_ADMIN')
    db.add(admin)
    write_audit(db, '', operator['id'], operator['name'], 'CREATE', 'tenant', t.id, {'name': name})
    return t
