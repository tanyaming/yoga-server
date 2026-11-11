"""点名消课：消课幂等（member+session 唯一约束），重复点名不重复扣。

扣课策略（MVP）：优先扣课时型权益（PACKAGE/PRIVATE），按到期时间 FIFO 扣 1 节；
课时耗尽则报课时不足。储值卡仅记账，本期不支持抵扣消课。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessRuleError, InsufficientEntitlement, NotFound
from ..models import (AttendanceRecord, ClassSession, Enrollment, Entitlement,
                      LedgerEntry)
from ..repositories.base import write_audit
from ..security import random_id
from ..utils.timeutil import now_naive


def _get_session(db: Session, tenant_id: str, session_id: str) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s or s.tenant_id != tenant_id:
        raise NotFound('课节不存在')
    return s


def get_roster(db: Session, tenant_id: str, session_id: str) -> Dict:
    """点名名单：已约 + 已到课 + 爽约的会员，含点名状态。"""
    s = _get_session(db, tenant_id, session_id)
    enrs = db.scalars(select(Enrollment).where(
        Enrollment.session_id == session_id,
        Enrollment.status.in_(('BOOKED', 'ATTENDED', 'NO_SHOW')))
        .order_by(Enrollment.created_at.asc())).all()
    from ..models import Member
    items = []
    for e in enrs:
        m = db.get(Member, e.member_id)
        att = db.scalar(select(AttendanceRecord).where(
            AttendanceRecord.session_id == session_id,
            AttendanceRecord.member_id == e.member_id))
        items.append({
            'enrollment_id': e.id, 'member_id': e.member_id,
            'member_name': m.name if m else '', 'phone_mask': m.phone_mask if m else '',
            'status': e.status, 'attended': att.status if att else '',
            'sessions_consumed': att.sessions_consumed if att else 1,
        })
    return {
        'session_id': s.id, 'course_name': s.course_name,
        'starts_at': s.starts_at.strftime('%Y-%m-%d %H:%M'),
        'capacity': s.capacity, 'booked': s.booked, 'items': items,
    }


def mark_attendance(db: Session, tenant_id: str, operator: Dict, session_id: str,
                    member_id: str, status: str = 'ATTENDED') -> Dict:
    if status not in ('ATTENDED', 'NO_SHOW'):
        raise BusinessRuleError('点名状态非法')
    s = _get_session(db, tenant_id, session_id)

    # 幂等：已点名直接返回，不重复扣课
    existing = db.scalar(select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == member_id))
    if existing:
        return {'id': existing.id, 'already': True, 'status': existing.status}

    enr = db.scalar(select(Enrollment).where(
        Enrollment.session_id == session_id, Enrollment.member_id == member_id,
        Enrollment.status == 'BOOKED'))
    if not enr:
        raise BusinessRuleError('该会员未预约此课或已处理')

    ledger_entry_id = ''
    if status == 'ATTENDED':
        ledger_entry_id = _consume_session(db, tenant_id, member_id, s, operator)
        enr.status = 'ATTENDED'
    else:
        enr.status = 'NO_SHOW'

    att = AttendanceRecord(id=random_id(), tenant_id=tenant_id, session_id=session_id,
                           enrollment_id=enr.id, member_id=member_id,
                           coach_id=operator['id'], store_id=s.store_id,
                           sessions_consumed=1, status=status,
                           ledger_entry_id=ledger_entry_id)
    db.add(att)

    write_audit(db, tenant_id, operator['id'], operator['name'], 'ATTEND', 'attendance', att.id,
                {'session_id': session_id, 'member_id': member_id, 'status': status})
    return {'id': att.id, 'already': False, 'status': status}


def _consume_session(db: Session, tenant_id: str, member_id: str,
                     s: ClassSession, operator: Dict) -> str:
    ents = db.scalars(select(Entitlement).where(
        Entitlement.tenant_id == tenant_id, Entitlement.member_id == member_id,
        Entitlement.status == 'ACTIVE', Entitlement.ptype.in_(('PACKAGE', 'PRIVATE')))
        .order_by(Entitlement.expires_at.asc(), Entitlement.created_at.asc())).all()
    for e in ents:
        if e.used_sessions < e.total_sessions:
            e.used_sessions += 1
            remain = e.total_sessions - e.used_sessions
            if remain == 0:
                e.status = 'DEPLETED'
            entry = LedgerEntry(id=random_id(), tenant_id=tenant_id, member_id=member_id,
                                entitlement_id=e.id, kind='CONSUME', delta_sessions=-1,
                                delta_cents=0, balance_sessions=remain, balance_cents=0,
                                ref_type='ATTENDANCE', ref_id=s.id,
                                remark='消课 %s' % s.course_name, operator_id=operator['id'])
            db.add(entry)
            return entry.id
    raise InsufficientEntitlement('该会员课时已用完，无法点名消课，请先续费')


def list_attendance(db: Session, tenant_id: str, session_id: str = '',
                    member_id: str = '') -> List[AttendanceRecord]:
    stmt = select(AttendanceRecord).where(AttendanceRecord.tenant_id == tenant_id)
    if session_id:
        stmt = stmt.where(AttendanceRecord.session_id == session_id)
    if member_id:
        stmt = stmt.where(AttendanceRecord.member_id == member_id)
    return list(db.scalars(stmt.order_by(AttendanceRecord.created_at.desc())).all())
