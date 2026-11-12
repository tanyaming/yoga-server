"""租户 / 门店 / 账号服务。账号含教练。"""
from __future__ import annotations

from typing import Dict, List, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import BusinessRuleError, Conflict, NotFound, ValidationError
from ..models import ClassSession, Store, Tenant, User
from ..repositories.base import write_audit
from ..security import hash_password, mask_phone, random_id
from ..utils.timeutil import now_naive

_ROLES = ('PLATFORM_ADMIN', 'TENANT_ADMIN', 'STORE_MANAGER', 'COACH')
# 可被馆主分配的角色（平台管理员不允许在租户内创建/授予）
_ASSIGNABLE_ROLES = ('TENANT_ADMIN', 'STORE_MANAGER', 'COACH')
ADMIN_ROLE = 'TENANT_ADMIN'


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


def get_store(db: Session, tenant_id: str, store_id: str) -> Store:
    """按租户取门店；跨租户与不存在统一 404，不泄露其他租户是否存在该 ID。"""
    store = db.get(Store, store_id)
    if not store or store.tenant_id != tenant_id:
        raise NotFound('门店不存在')
    return store


def update_store(db: Session, tenant_id: str, operator: Dict, store_id: str,
                 data: dict) -> Store:
    """T-24：馆主自助维护门店基础信息（名称/编码/地址/电话/营业时间）。"""
    store = get_store(db, tenant_id, store_id)
    changes: Dict[str, object] = {}

    if data.get('name') is not None:
        name = str(data.get('name')).strip()
        if not name:
            raise ValidationError('门店名称不能为空')
        store.name = name
        changes['name'] = name
    if data.get('code') is not None:
        code = str(data.get('code')).strip()
        if code and code != store.code:
            exists = db.scalar(select(Store).where(
                Store.tenant_id == tenant_id, Store.code == code, Store.id != store.id))
            if exists:
                raise Conflict('门店编码已存在')
            store.code = code
            changes['code'] = code
    for field in ('address', 'phone_mask', 'business_hours'):
        if data.get(field) is not None:
            setattr(store, field, str(data.get(field)).strip())
            changes[field] = getattr(store, field)

    if changes:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'UPDATE', 'store',
                    store.id, changes)
    return store


def count_future_sessions(db: Session, tenant_id: str, store_id: str) -> int:
    """该门店尚未结束的课节数（未来开课且未取消）。"""
    return int(db.scalar(select(func.count(ClassSession.id)).where(
        ClassSession.tenant_id == tenant_id,
        ClassSession.store_id == store_id,
        ClassSession.starts_at > now_naive(),
        ClassSession.status != 'CANCELLED')) or 0)


def disable_store(db: Session, tenant_id: str, operator: Dict, store_id: str) -> Store:
    """停用门店。仍有未结束课节时必须拒绝 —— 否则会员照常约课却上不了课。"""
    store = get_store(db, tenant_id, store_id)
    pending = count_future_sessions(db, tenant_id, store_id)
    if pending > 0:
        raise BusinessRuleError('该门店还有 %d 节未结束课程' % pending)
    if store.status != 'DISABLED':
        store.status = 'DISABLED'
        write_audit(db, tenant_id, operator['id'], operator['name'], 'DISABLE', 'store', store.id,
                    {'name': store.name, 'future_sessions': pending})
    return store


def list_stores(db: Session, tenant_id: str) -> List[Store]:
    return list(db.scalars(select(Store).where(Store.tenant_id == tenant_id)).all())


# ---------- 账号 ----------
def _check_role(role: str) -> str:
    role = (role or '').strip().upper()
    if role not in _ASSIGNABLE_ROLES:
        raise ValidationError('非法角色')
    return role


def get_user(db: Session, tenant_id: str, user_id: str) -> User:
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise NotFound('账号不存在')
    return user


def count_active_admins(db: Session, tenant_id: str) -> int:
    return int(db.scalar(select(func.count(User.id)).where(
        User.tenant_id == tenant_id, User.role == ADMIN_ROLE,
        User.status == 'ACTIVE')) or 0)


def create_user(db: Session, tenant_id: str, operator: Dict, data: dict) -> User:
    username = (data.get('username') or '').strip()
    role = data.get('role') or 'COACH'
    if not username:
        raise ValidationError('账号名不能为空')
    # 必须用 _ASSIGNABLE_ROLES 而不是全部角色：租户内建号不允许授予 PLATFORM_ADMIN，
    # 否则馆主可以自造一个跨租户平台账号完成提权（与 PATCH 的口径保持一致）。
    role = _check_role(role)
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


def update_user(db: Session, tenant_id: str, operator: Dict, user_id: str,
                data: dict) -> User:
    """T-24：改姓名/所属门店/角色。**不得把最后一位馆主降级**。"""
    user = get_user(db, tenant_id, user_id)
    changes: Dict[str, object] = {}

    if data.get('real_name') is not None:
        user.real_name = str(data.get('real_name')).strip()
        changes['real_name'] = user.real_name
    if data.get('store_id') is not None:
        store_id = str(data.get('store_id') or '').strip()
        if store_id:
            get_store(db, tenant_id, store_id)  # 校验门店存在且属于本租户
        user.store_id = store_id
        changes['store_id'] = store_id
    if data.get('role') is not None:
        new_role = _check_role(data.get('role'))
        if user.role == ADMIN_ROLE and new_role != ADMIN_ROLE and count_active_admins(db, tenant_id) <= 1:
            raise BusinessRuleError('至少保留一位馆主，不能降级最后一个馆主账号')
        user.role = new_role
        changes['role'] = new_role

    if changes:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'UPDATE', 'user', user.id,
                    changes)
    return user


def disable_user(db: Session, tenant_id: str, operator: Dict, user_id: str) -> User:
    """停用账号：禁止停用自己（否则把自己锁在门外），禁止停用最后一位馆主。"""
    user = get_user(db, tenant_id, user_id)
    if user.id == operator.get('id'):
        raise BusinessRuleError('不能停用自己的账号')
    if user.role == ADMIN_ROLE and user.status == 'ACTIVE' and count_active_admins(db, tenant_id) <= 1:
        raise BusinessRuleError('至少保留一位馆主，不能停用最后一个馆主账号')
    if user.status != 'DISABLED':
        user.status = 'DISABLED'
        write_audit(db, tenant_id, operator['id'], operator['name'], 'DISABLE', 'user', user.id,
                    {'username': user.username})
    return user


def reset_password(db: Session, tenant_id: str, operator: Dict, user_id: str, new_password: str) -> User:
    user = get_user(db, tenant_id, user_id)
    if not new_password or len(new_password) < 6:
        raise ValidationError('密码至少 6 位')
    user.password_hash = hash_password(new_password)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'UPDATE', 'user', user.id,
                {'action': 'reset_password'})
    return user


# ---------- 教练 ----------
def list_coaches(db: Session, tenant_id: str, include_disabled: bool = False) -> List[User]:
    """教练字典（排课/模板下拉用）。

    默认只返回启用中的账号：停用教练不应再被选为排课教练。
    但课节里已存在停用教练的名字映射需求，故保留 ``include_disabled`` 开关，
    由调用方按用途取（下拉=默认，名称映射=true）。
    """
    stmt = select(User).where(User.tenant_id == tenant_id, User.role == 'COACH')
    if not include_disabled:
        stmt = stmt.where(User.status == 'ACTIVE')
    return list(db.scalars(stmt).all())


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
