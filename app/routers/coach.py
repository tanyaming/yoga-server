"""教练端接口：今日课表 / 点名 / 会员跟进。"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..models import FollowUp, Member
from ..services import attendance as att_svc
from ..services import member as mem_svc
from ..services import schedule as sch_svc
from ..utils.timeutil import business_date

router = APIRouter(prefix='/api/coach', tags=['coach'])

coach = require_min_role('COACH')


class MarkBody(BaseModel):
    session_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    status: str = 'ATTENDED'


class FollowUpBody(BaseModel):
    member_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    visible_to_member: bool = False


@router.get('/today')
def today(auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    rows = sch_svc.list_sessions(db, auth.tenant_id, coach_id=auth.id,
                                 date=business_date())
    return [{'id': s.id, 'course_name': s.course_name, 'store_id': s.store_id,
             'starts_at': s.starts_at.strftime('%H:%M'), 'ends_at': s.ends_at.strftime('%H:%M'),
             'capacity': s.capacity, 'booked': s.booked, 'status': s.status} for s in rows]


@router.get('/sessions/{session_id}/roster')
def roster(session_id: str, auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    return att_svc.get_roster(db, auth.tenant_id, session_id)


@router.post('/attendance')
def mark(body: MarkBody, auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    result = att_svc.mark_attendance(db, auth.tenant_id, _op(auth), body.session_id,
                                     body.member_id, body.status)
    db.commit()
    return result


@router.get('/members')
def members(keyword: str = '', auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    return [{'id': m.id, 'name': m.name, 'phone_mask': m.phone_mask}
            for m in mem_svc.list_members(db, auth.tenant_id, keyword)]


@router.get('/follow-ups/{member_id}')
def follow_ups(member_id: str, auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    return [{'id': f.id, 'content': f.content, 'visible_to_member': f.visible_to_member,
             'created_at': f.created_at.strftime('%Y-%m-%d %H:%M')}
            for f in mem_svc.list_follow_ups(db, auth.tenant_id, member_id)]


@router.post('/follow-ups')
def add_follow_up(body: FollowUpBody, auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    f = mem_svc.add_follow_up(db, auth.tenant_id, _op(auth), body.member_id, body.model_dump())
    db.commit()
    return {'id': f.id}


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
