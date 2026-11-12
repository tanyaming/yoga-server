"""点名消课：消课幂等（member+session 唯一约束），重复点名不重复扣。

扣课策略（MVP）：优先扣课时型权益（PACKAGE/PRIVATE），按到期时间 FIFO 扣 1 节；
课时耗尽则报课时不足。储值卡仅记账，本期不支持抵扣消课。

FR-17：点名限定在课程时段 ±N 分钟（可配置 ATTENDANCE_WINDOW_MINUTES，0=不限）；
教练代临时到店会员登记「代约待办」并提示店长补录权益，不绕过权益直接扣课。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import BusinessRuleError, Conflict, InsufficientEntitlement, NotFound, ValidationError
from ..models import (AttendanceRecord, ClassSession, Enrollment, Entitlement,
                      FollowUp, LedgerEntry, Member)
from ..repositories.base import write_audit
from ..security import random_id
from ..utils.timeutil import now_naive
# 点名会让会员退出占位，需要复用排课服务的名额重算口径（schedule 不反向依赖本模块）
from . import schedule as schedule_service


def _get_session(db: Session, tenant_id: str, session_id: str) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s or s.tenant_id != tenant_id:
        raise NotFound('课节不存在')
    return s


def within_attendance_window(s: ClassSession, now=None, minutes: Optional[int] = None) -> bool:
    """FR-17 纯函数：是否在允许点名的时间窗内。可独立传 minutes 做边界验证。"""
    m = settings.ATTENDANCE_WINDOW_MINUTES if minutes is None else minutes
    if m <= 0:
        return True
    cur = now or now_naive()
    return (s.starts_at - timedelta(minutes=m)) <= cur <= (s.ends_at + timedelta(minutes=m))


def get_roster(db: Session, tenant_id: str, session_id: str) -> Dict:
    """点名名单：已约 + 已到课 + 爽约的会员，含点名状态。"""
    s = _get_session(db, tenant_id, session_id)
    enrs = db.scalars(select(Enrollment).where(
        Enrollment.session_id == session_id,
        Enrollment.status.in_(('BOOKED', 'OFFERED', 'ATTENDED', 'NO_SHOW')))
        .order_by(Enrollment.created_at.asc())).all()
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
        'within_window': within_attendance_window(s),
        'window_minutes': settings.ATTENDANCE_WINDOW_MINUTES,
    }


def mark_attendance(db: Session, tenant_id: str, operator: Dict, session_id: str,
                    member_id: str, status: str = 'ATTENDED') -> Dict:
    if status not in ('ATTENDED', 'NO_SHOW'):
        raise BusinessRuleError('点名状态非法')
    s = _get_session(db, tenant_id, session_id)

    # 幂等优先：已点名直接返回，不重复扣课，也不受时间窗影响
    existing = db.scalar(select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == member_id))
    if existing:
        return {'id': existing.id, 'already': True, 'status': existing.status}

    # FR-17：点名时间窗（服务端最终校验）
    if not within_attendance_window(s):
        m = settings.ATTENDANCE_WINDOW_MINUTES
        raise BusinessRuleError('不在点名时间窗内（开课前 %d 分钟至结束后 %d 分钟）' % (m, m))

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

    # 点名后该会员退出 BOOKED/OFFERED，占位立即回落：不重算会让「已约」虚高、
    # 课节一直挂在 FULL 上（名额口径 T-03 的组成部分）。
    db.flush()
    schedule_service.recount_session_booked(db, tenant_id, session_id)

    write_audit(db, tenant_id, operator['id'], operator['name'], 'ATTEND', 'attendance', att.id,
                {'session_id': session_id, 'member_id': member_id, 'status': status})
    return {'id': att.id, 'already': False, 'status': status}


def add_walk_in(db: Session, tenant_id: str, operator: Dict, session_id: str,
                member_id: str, note: str = '') -> Dict:
    """FR-17 教练代约：登记跟进待办并提示店长补录权益，不直接扣课、不占名额。"""
    s = _get_session(db, tenant_id, session_id)
    if s.status == 'CANCELLED':
        raise BusinessRuleError('该课程已取消')
    m = db.get(Member, member_id)
    if not m or m.tenant_id != tenant_id:
        raise NotFound('会员不存在')
    exists = db.scalar(select(Enrollment).where(
        Enrollment.tenant_id == tenant_id, Enrollment.session_id == session_id,
        Enrollment.member_id == member_id,
        Enrollment.status.in_(('BOOKED', 'WAITLIST', 'OFFERED', 'ATTENDED', 'NO_SHOW'))))
    if exists:
        raise Conflict('该会员已有预约或点名记录，请直接点名')
    content = ('[代约待办] %s %s：会员 %s 临时到店，请店长补录权益后再点名消课。%s'
               % (s.course_name, s.starts_at.strftime('%m-%d %H:%M'), m.name, note[:100]))
    f = FollowUp(id=random_id(), tenant_id=tenant_id, member_id=member_id,
                 coach_id=operator['id'], content=content,
                 visible_to_member=False, created_by=operator['id'])
    db.add(f)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'WALK_IN', 'follow_up', f.id,
                {'session_id': session_id, 'member_id': member_id})
    return {'id': f.id, 'can_mark': False,
            'message': '已登记代约待办，请店长补录权益后再点名消课'}


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
