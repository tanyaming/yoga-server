"""租户 / 门店 / 账号 / 教练路由（馆主权限）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import tenant as svc

router = APIRouter(prefix='/api/tenant', tags=['tenant'])

admin = require_min_role('TENANT_ADMIN')


class StoreBody(BaseModel):
    name: str = Field(min_length=1)
    code: str = ''
    address: str = ''
    phone_mask: str = ''
    business_hours: str = '09:00-21:00'


class UserBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = '123456'
    real_name: str = ''
    role: str = 'COACH'
    store_id: str = ''


class ResetBody(BaseModel):
    new_password: str = Field(min_length=6)


@router.get('/stores')
def list_stores(auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    return [{'id': s.id, 'code': s.code, 'name': s.name, 'address': s.address,
             'business_hours': s.business_hours} for s in svc.list_stores(db, auth.tenant_id)]


@router.post('/stores')
def create_store(body: StoreBody, auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    s = svc.create_store(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': s.id, 'name': s.name}


@router.get('/users')
def list_users(role: str = '', auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    return [{'id': u.id, 'username': u.username, 'real_name': u.real_name, 'role': u.role,
             'store_id': u.store_id, 'status': u.status}
            for u in svc.list_users(db, auth.tenant_id, role)]


@router.post('/users')
def create_user(body: UserBody, auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    u = svc.create_user(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': u.id, 'username': u.username, 'role': u.role}


@router.post('/users/{user_id}/reset-password')
def reset_password(user_id: str, body: ResetBody, auth: AuthUser = Depends(admin),
                   db: Session = Depends(get_db)):
    svc.reset_password(db, auth.tenant_id, _op(auth), user_id, body.new_password)
    db.commit()
    return {'ok': True}


@router.get('/coaches')
def list_coaches(auth: AuthUser = Depends(admin), db: Session = Depends(get_db)):
    return [{'id': u.id, 'real_name': u.real_name, 'store_id': u.store_id}
            for u in svc.list_coaches(db, auth.tenant_id)]


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
