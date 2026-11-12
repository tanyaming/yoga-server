"""商品与收款登记路由（店长及以上权限）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import payment as svc

router = APIRouter(prefix='/api/payments', tags=['payments'])

mgr = require_min_role('STORE_MANAGER')


class ProductBody(BaseModel):
    name: str = Field(min_length=1)
    ptype: str = 'PACKAGE'
    total_sessions: int = 0
    price_cents: int = 0
    valid_days: int = 365
    cross_store: bool = False
    applicable_store_ids: str = ''   # 逗号分隔，空=不限门店
    allowed_ctypes: str = ''         # GROUP/PRIVATE，空=不限
    description: str = ''


class PaymentBody(BaseModel):
    store_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)
    method: str = 'CASH'
    paid_at: str = ''
    receipt_no: str = ''
    idempotency_key: str = ''
    remark: str = ''


class RefundBody(BaseModel):
    entitlement_id: str = Field(min_length=1)
    remark: str = ''


# ---------- 商品 ----------
@router.get('/products')
def list_products(auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return [{'id': p.id, 'code': p.code, 'name': p.name, 'ptype': p.ptype,
             'total_sessions': p.total_sessions, 'price_cents': p.price_cents,
             'valid_days': p.valid_days, 'cross_store': p.cross_store,
             'applicable_store_ids': p.applicable_store_ids,
             'allowed_ctypes': p.allowed_ctypes, 'description': p.description}
            for p in svc.list_products(db, auth.tenant_id)]


@router.post('/products')
def create_product(body: ProductBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    p = svc.create_product(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': p.id, 'name': p.name, 'code': p.code}


# ---------- 收款 ----------
@router.get('')
def list_payments(store_id: str = '', member_id: str = '',
                  auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.list_payments(db, auth.tenant_id, store_id, member_id)


@router.post('')
def register_payment(body: PaymentBody, auth: AuthUser = Depends(mgr),
                     db: Session = Depends(get_db)):
    result = svc.register_payment(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return result


@router.post('/refund')
def refund(body: RefundBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    result = svc.refund(db, auth.tenant_id, _op(auth), body.entitlement_id, body.remark)
    db.commit()
    return result


# ---------- 会员权益 / 流水（后台查看） ----------
@router.get('/entitlements/{member_id}')
def member_entitlements(member_id: str, auth: AuthUser = Depends(mgr),
                        db: Session = Depends(get_db)):
    return svc.member_entitlements(db, auth.tenant_id, member_id)


@router.get('/ledger/{member_id}')
def member_ledger(member_id: str, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.member_ledger(db, auth.tenant_id, member_id)


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
