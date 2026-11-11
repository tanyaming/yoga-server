"""排课 / 约课路由（后台，店长及以上）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import schedule as svc

router = APIRouter(prefix='/api/schedule', tags=['schedule'])

mgr = require_min_role('STORE_MANAGER')


class TemplateBody(BaseModel):
    name: str = Field(min_length=1)
    store_id: str = ''
    coach_id: str = ''
    ctype: str = 'GROUP'
    capacity: int = 15
    duration_minutes: int = 60


class SessionBody(BaseModel):
    template_id: str = Field(min_length=1)
    starts_at: str = Field(min_length=1)
    cancel_deadline_hours: int = 2


# ---------- 课程模板 ----------
@router.get('/templates')
def list_templates(store_id: str = '', auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return [{'id': t.id, 'code': t.code, 'name': t.name, 'store_id': t.store_id,
             'coach_id': t.coach_id, 'ctype': t.ctype, 'capacity': t.capacity,
             'duration_minutes': t.duration_minutes}
            for t in svc.list_templates(db, auth.tenant_id, store_id)]


@router.post('/templates')
def create_template(body: TemplateBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    t = svc.create_template(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': t.id, 'name': t.name}


# ---------- 排期 ----------
@router.get('/sessions')
def list_sessions(store_id: str = '', date: str = '', coach_id: str = '',
                  auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    rows = svc.list_sessions(db, auth.tenant_id, store_id, date, coach_id)
    return [{'id': s.id, 'course_name': s.course_name, 'store_id': s.store_id,
             'coach_id': s.coach_id, 'starts_at': s.starts_at.strftime('%Y-%m-%d %H:%M'),
             'ends_at': s.ends_at.strftime('%H:%M'), 'capacity': s.capacity,
             'booked': s.booked, 'status': s.status} for s in rows]


@router.post('/sessions')
def create_session(body: SessionBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    s = svc.create_session(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': s.id, 'course_name': s.course_name}


@router.post('/sessions/{session_id}/cancel')
def cancel_session(session_id: str, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    s = svc.cancel_session(db, auth.tenant_id, _op(auth), session_id)
    db.commit()
    return {'id': s.id, 'status': s.status}


@router.get('/sessions/{session_id}')
def session_detail(session_id: str, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.session_detail(db, auth.tenant_id, session_id)


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
