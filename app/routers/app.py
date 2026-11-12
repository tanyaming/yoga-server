"""会员端小程序接口：课程表 / 约课 / 我的权益 / 流水。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_member
from ..models import Member
from ..services import payment as pay_svc
from ..services import schedule as sch_svc
from ..utils.timeutil import business_date, parse_dt

router = APIRouter(prefix='/api/app', tags=['app'])


class BookBody(BaseModel):
    session_id: str = Field(min_length=1)


class CancelBody(BaseModel):
    enrollment_id: str = Field(min_length=1)


@router.get('/me')
def me(member: Member = Depends(get_current_member), db: Session = Depends(get_db)):
    ents = pay_svc.member_entitlements(db, member.tenant_id, member.id)
    remain = sum(e['remain_sessions'] for e in ents if e['ptype'] in ('PACKAGE', 'PRIVATE'))
    balance = sum(e['balance_cents'] for e in ents if e['ptype'] == 'STORED')
    return {'id': member.id, 'name': member.name, 'phone_mask': member.phone_mask,
            'tenant_id': member.tenant_id, 'remain_sessions': remain,
            'balance_cents': balance, 'entitlements': ents}


@router.get('/sessions')
def sessions(store_id: str = '', date: str = '',
             member: Member = Depends(get_current_member), db: Session = Depends(get_db)):
    # 会员端默认只看「今天及未来」，避免历史课节混进课程表；
    # 主动传 date 时按指定日期查询（含历史），供日期条回看与后台核对。
    since = None if date else parse_dt(business_date())
    rows = sch_svc.list_sessions(db, member.tenant_id, store_id, date, since=since)
    out = []
    for s in rows:
        d = sch_svc.session_detail(db, member.tenant_id, s.id, member.id)
        out.append(d)
    return out


@router.post('/book')
def book(body: BookBody, member: Member = Depends(get_current_member),
         db: Session = Depends(get_db)):
    result = sch_svc.book(db, member.tenant_id, member.id, body.session_id, 'MEMBER')
    db.commit()
    return result


@router.post('/cancel')
def cancel(body: CancelBody, member: Member = Depends(get_current_member),
           db: Session = Depends(get_db)):
    result = sch_svc.cancel_booking(db, member.tenant_id, member.id, body.enrollment_id)
    db.commit()
    return result


@router.get('/bookings')
def bookings(member: Member = Depends(get_current_member), db: Session = Depends(get_db)):
    return sch_svc.my_bookings(db, member.tenant_id, member.id)


@router.get('/entitlements')
def entitlements(member: Member = Depends(get_current_member), db: Session = Depends(get_db)):
    return pay_svc.member_entitlements(db, member.tenant_id, member.id)


@router.get('/ledger')
def ledger(member: Member = Depends(get_current_member), db: Session = Depends(get_db)):
    return pay_svc.member_ledger(db, member.tenant_id, member.id)
