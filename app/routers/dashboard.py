"""经营看板 / 账实核对路由（店长及以上）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import dashboard as svc

router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])

mgr = require_min_role('STORE_MANAGER')


class ReconcileBody(BaseModel):
    store_id: str = Field(min_length=1)
    date: str = Field(min_length=8)


class ResolveBody(BaseModel):
    note: str = ''


@router.get('/overview')
def overview(store_id: str = '', auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.get_overview(db, auth.tenant_id, store_id)


@router.get('/trend')
def trend(store_id: str = '', days: int = 7, auth: AuthUser = Depends(mgr),
          db: Session = Depends(get_db)):
    return svc.get_trend(db, auth.tenant_id, store_id, days)


@router.get('/stores')
def store_comparison(auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.get_store_comparison(db, auth.tenant_id)


@router.get('/renewals')
def renewals(auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.get_renewal_list(db, auth.tenant_id)


@router.post('/reconcile')
def reconcile(body: ReconcileBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    result = svc.reconcile_daily(db, auth.tenant_id, body.store_id, body.date)
    db.commit()
    return result


@router.get('/diffs')
def list_diffs(status: str = 'PENDING', auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.list_diffs(db, auth.tenant_id, status)


@router.post('/diffs/{diff_id}/resolve')
def resolve_diff(diff_id: str, body: ResolveBody, auth: AuthUser = Depends(mgr),
                 db: Session = Depends(get_db)):
    result = svc.resolve_diff(db, auth.tenant_id, _op(auth), diff_id, body.note)
    db.commit()
    return result


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
