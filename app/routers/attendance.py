"""点名消课路由（教练及以上权限）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import attendance as svc

router = APIRouter(prefix='/api/attendance', tags=['attendance'])

coach = require_min_role('COACH')


class MarkBody(BaseModel):
    session_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    status: str = 'ATTENDED'


@router.get('/sessions/{session_id}/roster')
def get_roster(session_id: str, auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    return svc.get_roster(db, auth.tenant_id, session_id)


@router.post('/mark')
def mark_attendance(body: MarkBody, auth: AuthUser = Depends(coach), db: Session = Depends(get_db)):
    result = svc.mark_attendance(db, auth.tenant_id, _op(auth), body.session_id,
                                 body.member_id, body.status)
    db.commit()
    return result


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
