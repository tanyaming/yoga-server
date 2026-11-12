"""会员管理路由（店长及以上权限）。"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import member as svc

router = APIRouter(prefix='/api/members', tags=['members'])

mgr = require_min_role('STORE_MANAGER')


class MemberBody(BaseModel):
    phone: str = Field(min_length=11, max_length=11, pattern=r'^1[3-9]\d{9}$')
    name: str = Field(min_length=1, max_length=60)
    source_store_id: str = ''
    note: str = ''


class MemberPatch(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None


class ImportRow(BaseModel):
    phone: str
    name: str
    source_store_id: str = ''
    note: str = ''


class ImportBody(BaseModel):
    rows: List[ImportRow]


class FollowUpBody(BaseModel):
    content: str = Field(min_length=1)
    visible_to_member: bool = False


@router.get('')
def list_members(keyword: str = '', auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return [{'id': m.id, 'name': m.name, 'phone_mask': m.phone_mask,
             'source_store_id': m.source_store_id, 'status': m.status,
             'created_at': m.created_at.strftime('%Y-%m-%d')}
            for m in svc.list_members(db, auth.tenant_id, keyword)]


@router.post('')
def create_member(body: MemberBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    m = svc.create_member(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': m.id, 'name': m.name, 'phone_mask': m.phone_mask}


@router.get('/{member_id}')
def get_member(member_id: str, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    m = svc.get_member(db, auth.tenant_id, member_id)
    return {'id': m.id, 'name': m.name, 'phone_mask': m.phone_mask,
            'source_store_id': m.source_store_id, 'status': m.status, 'note': m.note}


@router.patch('/{member_id}')
def update_member(member_id: str, body: MemberPatch, auth: AuthUser = Depends(mgr),
                  db: Session = Depends(get_db)):
    m = svc.update_member(db, auth.tenant_id, _op(auth), member_id,
                          body.model_dump(exclude_none=True))
    db.commit()
    return {'id': m.id, 'name': m.name}


@router.post('/import')
def import_members(body: ImportBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    result = svc.import_members(db, auth.tenant_id, _op(auth), [r.model_dump() for r in body.rows])
    db.commit()
    return result


@router.get('/{member_id}/follow-ups')
def list_follow_ups(member_id: str, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return [{'id': f.id, 'content': f.content, 'visible_to_member': f.visible_to_member,
             'coach_id': f.coach_id, 'created_at': f.created_at.strftime('%Y-%m-%d %H:%M')}
            for f in svc.list_follow_ups(db, auth.tenant_id, member_id)]


@router.post('/{member_id}/follow-ups')
def add_follow_up(member_id: str, body: FollowUpBody, auth: AuthUser = Depends(mgr),
                  db: Session = Depends(get_db)):
    f = svc.add_follow_up(db, auth.tenant_id, _op(auth), member_id, body.model_dump())
    db.commit()
    return {'id': f.id}


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
