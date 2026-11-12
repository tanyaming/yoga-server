"""调课 / 停课申请与审批（FR-12）。

流程：教练或店长提交申请（PENDING）→ 馆主/店长审批 → 通过后变更排期
并自动通知全部已约会员（FR-11 排期变更通知）→ 写入审计。
审批是唯一可改变已存在排期的入口，避免教练单方面改动引发会员纠纷。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessRuleError, Conflict, NotFound, ValidationError
from ..models import (ClassSession, Enrollment, ScheduleChangeRequest)
from ..models.change_request import CHANGE_STATUS, CHANGE_TYPES
from ..repositories.base import write_audit
from ..security import random_id
from ..services.notification import notify
from ..utils.timeutil import now_naive, parse_dt


def _get_session(db: Session, tenant_id: str, session_id: str) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s or s.tenant_id != tenant_id:
        raise NotFound('课节不存在')
    return s


def _get_request(db: Session, tenant_id: str, request_id: str) -> ScheduleChangeRequest:
    r = db.get(ScheduleChangeRequest, request_id)
    if not r or r.tenant_id != tenant_id:
        raise NotFound('调课申请不存在')
    return r


def request_change(db: Session, tenant_id: str, operator: Dict, data: dict) -> ScheduleChangeRequest:
    session_id = (data.get('session_id') or '').strip()
    s = _get_session(db, tenant_id, session_id)
    if s.status == 'CANCELLED':
        raise BusinessRuleError('该课程已取消，无需再申请调课')
    ctype = (data.get('change_type') or 'RESCHEDULE').upper()
    if ctype not in CHANGE_TYPES:
        raise ValidationError('调课类型非法')
    new_starts_at = parse_dt(data['new_starts_at']) if data.get('new_starts_at') else None
    new_coach_id = (data.get('new_coach_id') or '').strip()
    if ctype == 'RESCHEDULE' and new_starts_at is None:
        raise ValidationError('改期必须提供新的上课时间')
    if ctype == 'COACH_CHANGE' and not new_coach_id:
        raise ValidationError('换教练必须提供新教练 ID')
    dup = db.scalar(select(ScheduleChangeRequest).where(
        ScheduleChangeRequest.tenant_id == tenant_id,
        ScheduleChangeRequest.session_id == session_id,
        ScheduleChangeRequest.change_type == ctype,
        ScheduleChangeRequest.status == 'PENDING'))
    if dup:
        raise Conflict('该课节已有待审批的同类申请')
    r = ScheduleChangeRequest(
        id=random_id(), tenant_id=tenant_id, session_id=session_id,
        requester_id=operator['id'], requester_name=operator['name'],
        change_type=ctype, new_starts_at=new_starts_at, new_coach_id=new_coach_id,
        reason=(data.get('reason') or '')[:500], status='PENDING',
    )
    db.add(r)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'REQUEST_CHANGE',
                'change_request', r.id, {'session_id': session_id, 'change_type': ctype})
    return r


def list_requests(db: Session, tenant_id: str, status: str = '',
                  session_id: str = '') -> List[Dict]:
    stmt = select(ScheduleChangeRequest).where(
        ScheduleChangeRequest.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(ScheduleChangeRequest.status == status)
    if session_id:
        stmt = stmt.where(ScheduleChangeRequest.session_id == session_id)
    rows = db.scalars(stmt.order_by(ScheduleChangeRequest.created_at.desc())).all()
    return [{
        'id': r.id, 'session_id': r.session_id, 'change_type': r.change_type,
        'new_starts_at': r.new_starts_at.strftime('%Y-%m-%d %H:%M') if r.new_starts_at else '',
        'new_coach_id': r.new_coach_id, 'reason': r.reason, 'status': r.status,
        'requester_name': r.requester_name, 'review_note': r.review_note,
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
    } for r in rows]


def approve(db: Session, tenant_id: str, operator: Dict, request_id: str,
            note: str = '') -> Dict:
    r = _get_request(db, tenant_id, request_id)
    if r.status != 'PENDING':
        raise Conflict('该申请已处理，不可重复审批')
    s = _get_session(db, tenant_id, r.session_id)
    duration = s.ends_at - s.starts_at
    changed = ''
    if r.change_type == 'CANCEL':
        s.status = 'CANCELLED'
        changed = '课程已停课'
    elif r.change_type == 'RESCHEDULE' and r.new_starts_at:
        s.starts_at = r.new_starts_at
        s.ends_at = r.new_starts_at + duration
        changed = '上课时间改为 %s' % r.new_starts_at.strftime('%m-%d %H:%M')
    elif r.change_type == 'COACH_CHANGE':
        s.coach_id = r.new_coach_id
        changed = '授课教练已调整'
    r.status = 'APPROVED'
    r.approver_id = operator['id']
    r.review_note = note[:240]
    r.approved_at = now_naive()
    # FR-11 / FR-12：排期变更自动通知全部有效预约会员（含候补）
    enrs = db.scalars(select(Enrollment).where(
        Enrollment.tenant_id == tenant_id, Enrollment.session_id == s.id,
        Enrollment.status.in_(('BOOKED', 'WAITLIST', 'OFFERED')))).all()
    for e in enrs:
        notify(db, tenant_id, e.member_id, '课程变更通知',
               '%s：%s，请留意并按需调整预约' % (s.course_name, changed),
               'SESSION_CANCEL' if r.change_type == 'CANCEL' else 'SESSION_CHANGE',
               ref_id=s.id)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'APPROVE_CHANGE',
                'change_request', r.id, {'session_id': s.id, 'notified': len(enrs)})
    return {'id': r.id, 'status': 'APPROVED', 'session_status': s.status,
            'notified': len(enrs), 'changed': changed}


def reject(db: Session, tenant_id: str, operator: Dict, request_id: str,
           note: str = '') -> Dict:
    r = _get_request(db, tenant_id, request_id)
    if r.status != 'PENDING':
        raise Conflict('该申请已处理，不可重复审批')
    r.status = 'REJECTED'
    r.approver_id = operator['id']
    r.review_note = (note or '未通过')[:240]
    r.approved_at = now_naive()
    write_audit(db, tenant_id, operator['id'], operator['name'], 'REJECT_CHANGE',
                'change_request', r.id, {})
    return {'id': r.id, 'status': 'REJECTED'}
