"""调课 / 停课申请与审批路由（FR-12）。

教练可提交申请，店长及以上审批；审批通过后变更排期并自动通知已约会员。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import change_request as svc

router = APIRouter(prefix='/api/change-requests', tags=['change-requests'])

submitter = require_min_role('COACH')
approver = require_min_role('STORE_MANAGER')


class RequestBody(BaseModel):
    session_id: str = Field(min_length=1)
    change_type: str = 'RESCHEDULE'      # RESCHEDULE / COACH_CHANGE / CANCEL
    new_starts_at: str = ''
    new_coach_id: str = ''
    reason: str = ''


class ReviewBody(BaseModel):
    note: str = ''


@router.get('')
def list_requests(status: str = '', session_id: str = '',
                  auth: AuthUser = Depends(submitter), db: Session = Depends(get_db)):
    return svc.list_requests(db, auth.tenant_id, status, session_id)


@router.post('')
def request_change(body: RequestBody, auth: AuthUser = Depends(submitter),
                   db: Session = Depends(get_db)):
    r = svc.request_change(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': r.id, 'status': r.status, 'change_type': r.change_type}


@router.post('/{request_id}/approve')
def approve(request_id: str, body: ReviewBody, auth: AuthUser = Depends(approver),
            db: Session = Depends(get_db)):
    result = svc.approve(db, auth.tenant_id, _op(auth), request_id, body.note)
    db.commit()
    return result


@router.post('/{request_id}/reject')
def reject(request_id: str, body: ReviewBody, auth: AuthUser = Depends(approver),
           db: Session = Depends(get_db)):
    result = svc.reject(db, auth.tenant_id, _op(auth), request_id, body.note)
    db.commit()
    return result


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
