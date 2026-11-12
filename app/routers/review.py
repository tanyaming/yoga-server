"""后台评价审核路由（FR-20，店长及以上）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import review as svc

router = APIRouter(prefix='/api/reviews', tags=['reviews'])
mgr = require_min_role('STORE_MANAGER')


class ModerateBody(BaseModel):
    action: str = Field(min_length=1)  # PUBLISH / HIDE
    reason: str = ''


@router.get('')
def list_reviews(status: str = '', auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.list_all(db, auth.tenant_id, status)


@router.post('/{review_id}/moderate')
def moderate(review_id: str, body: ModerateBody, auth: AuthUser = Depends(mgr),
             db: Session = Depends(get_db)):
    result = svc.moderate(db, auth.tenant_id, _op(auth), review_id, body.action, body.reason)
    db.commit()
    return result


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
