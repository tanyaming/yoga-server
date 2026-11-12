"""租户 / 门店 / 账号 / 教练路由（馆主权限）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import tenant as svc

router = APIRouter(prefix='/api/tenant', tags=['tenant'])

admin = require_min_role('TENANT_ADMIN')
# 只读字典：门店名 / 教练名是排课、收款、账实核对页的公共依赖，
# 店长与教练都必须能读到（写操作仍然只对馆主开放）。
reader = require_min_role('COACH')


class StoreBody(BaseModel):
    name: str = Field(min_length=1)
    code: str = ''
    address: str = ''
    phone_mask: str = ''
    business_hours: str = '09:00-21:00'


class StoreUpdateBody(BaseModel):
    """门店更新：字段全可选，未传即保持原值（PATCH 语义的 PUT）。"""
    name: Optional[str] = None
    code: Optional[str] = None
    address: Optional[str] = None
    phone_mask: Optional[str] = None
    business_hours: Optional[str] = None


class UserBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = '123456'
    real_name: str = ''
    role: str = 'COACH'
    store_id: str = ''


class UserUpdateBody(BaseModel):
    """账号更新：只允许改姓名 / 所属门店 / 角色。"""
    real_name: Optional[str] = None
    store_id: Optional[str] = None
    role: Optional[str] = None


class ResetBody(BaseModel):
    new_password: str = Field(min_length=6)


def _store_dict(s) -> dict:
    return {'id': s.id, 'code': s.code, 'name': s.name, 'address': s.address,
            'phone_mask': s.phone_mask, 'business_hours': s.business_hours,
            'status': s.status}


def _user_dict(u) -> dict:
    return {'id': u.id, 'username': u.username, 'real_name': u.real_name, 'role': u.role,
            'store_id': u.store_id, 'status': u.status}


@router.get('/stores')
def list_stores(auth: AuthUser = Depends(reader), db: Session = Depends(get_db)):
    return [_store_dict(s) for s in svc.list_stores(db, auth.tenant_id)]


@router.post('/stores')
def create_store(body: StoreBody, auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    s = svc.create_store(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': s.id, 'name': s.name}


@router.put('/stores/{store_id}')
def update_store(store_id: str, body: StoreUpdateBody, auth: AuthUser = Depends(admin),
                 db: Session = Depends(get_db)):
    s = svc.update_store(db, auth.tenant_id, _op(auth), store_id, body.model_dump())
    db.commit()
    return _store_dict(s)


@router.post('/stores/{store_id}/disable')
def disable_store(store_id: str, auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    s = svc.disable_store(db, auth.tenant_id, _op(auth), store_id)
    db.commit()
    return {'id': s.id, 'status': s.status}


@router.get('/users')
def list_users(role: str = '', auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    return [_user_dict(u) for u in svc.list_users(db, auth.tenant_id, role)]


@router.post('/users')
def create_user(body: UserBody, auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    u = svc.create_user(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': u.id, 'username': u.username, 'role': u.role}


@router.patch('/users/{user_id}')
def update_user(user_id: str, body: UserUpdateBody, auth: AuthUser = Depends(admin),
                db: Session = Depends(get_db)):
    u = svc.update_user(db, auth.tenant_id, _op(auth), user_id, body.model_dump())
    db.commit()
    return _user_dict(u)


@router.post('/users/{user_id}/disable')
def disable_user(user_id: str, auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    u = svc.disable_user(db, auth.tenant_id, _op(auth), user_id)
    db.commit()
    return {'id': u.id, 'status': u.status}


@router.post('/users/{user_id}/reset-password')
def reset_password(user_id: str, body: ResetBody, auth: AuthUser = Depends(admin),
                   db: Session = Depends(get_db)):
    svc.reset_password(db, auth.tenant_id, _op(auth), user_id, body.new_password)
    db.commit()
    return {'ok': True}


@router.get('/coaches')
def list_coaches(include_disabled: bool = False, auth: AuthUser = Depends(reader),
                 db: Session = Depends(get_db)):
    """教练字典。默认只给启用中的（下拉用）；include_disabled=true 供历史课节做名称映射。"""
    return [{'id': u.id, 'real_name': u.real_name, 'store_id': u.store_id, 'status': u.status}
            for u in svc.list_coaches(db, auth.tenant_id, include_disabled)]


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
