"""排课 / 约课 / 候补 / 取消：约课一致性的核心服务。

约课五态：BOOKED 已约 / WAITLIST 候补 / CANCELLED 已取消 / ATTENDED 已到课 / NO_SHOW 爽约。
规则（可配置）：开课前 2h 截止取消；累计 3 次爽约限约 7 天。
权益口径（PRD）：会员租户内唯一、跨店通用，约课时预校验可用课时/余额，点名时才真正扣。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import (BookingClosed, BusinessRuleError, DuplicateBooking, NotFound,
                      ValidationError)
from ..models import (AttendanceRecord, ClassSession, CourseTemplate, Enrollment,
                      Entitlement, InAppMessage, Store, User)
from ..repositories.base import write_audit
from ..security import random_id
from ..utils.timeutil import now_naive, parse_dt

NO_SHOW_LIMIT = 3
NO_SHOW_WINDOW_DAYS = 7


# ---------- 课程模板 ----------
def create_template(db: Session, tenant_id: str, operator: Dict, data: dict) -> CourseTemplate:
    name = (data.get('name') or '').strip()
    if not name:
        raise ValidationError('课程名称不能为空')
    t = CourseTemplate(id=random_id(), tenant_id=tenant_id, code=data.get('code') or ('C' + random_id()[:6].upper()),
                       name=name, store_id=data.get('store_id', ''), coach_id=data.get('coach_id', ''),
                       ctype=data.get('ctype') or 'GROUP',
                       capacity=int(data.get('capacity', 15) or 15),
                       duration_minutes=int(data.get('duration_minutes', 60) or 60))
    db.add(t)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'course', t.id, {'name': name})
    return t


def list_templates(db: Session, tenant_id: str, store_id: str = '') -> List[CourseTemplate]:
    stmt = select(CourseTemplate).where(CourseTemplate.tenant_id == tenant_id)
    if store_id:
        stmt = stmt.where(CourseTemplate.store_id == store_id)
    return list(db.scalars(stmt).all())


# ---------- 排期 ----------
def create_session(db: Session, tenant_id: str, operator: Dict, data: dict) -> ClassSession:
    template = db.get(CourseTemplate, data.get('template_id', ''))
    if not template or template.tenant_id != tenant_id:
        raise NotFound('课程模板不存在')
    starts_at = parse_dt(data['starts_at'])
    ends_at = starts_at + timedelta(minutes=template.duration_minutes)
    s = ClassSession(id=random_id(), tenant_id=tenant_id, template_id=template.id,
                     store_id=template.store_id, coach_id=template.coach_id,
                     course_name=template.name, starts_at=starts_at, ends_at=ends_at,
                     capacity=template.capacity,
                     cancel_deadline_hours=int(data.get('cancel_deadline_hours', 2) or 2))
    db.add(s)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'session', s.id,
                {'course': template.name, 'starts_at': starts_at.strftime('%Y-%m-%d %H:%M')})
    return s


def list_sessions(db: Session, tenant_id: str, store_id: str = '', date: str = '',
                  coach_id: str = '', since=None) -> List[ClassSession]:
    """排期查询。

    date 有值时按单日精确查询（会员主动切换日期、后台核对历史均可用）；
    since 为 datetime 时限制时间下界，用于会员端默认「今天及未来」，
    避免把历史课节混进「今日课程」。两者可叠加，不改动后台历史查询口径。
    """
    stmt = select(ClassSession).where(ClassSession.tenant_id == tenant_id)
    if store_id:
        stmt = stmt.where(ClassSession.store_id == store_id)
    if coach_id:
        stmt = stmt.where(ClassSession.coach_id == coach_id)
    if date:
        d = parse_dt(date)
        nxt = d + timedelta(days=1)
        stmt = stmt.where(ClassSession.starts_at >= d, ClassSession.starts_at < nxt)
    if since is not None:
        stmt = stmt.where(ClassSession.starts_at >= since)
    stmt = stmt.order_by(ClassSession.starts_at.asc())
    return list(db.scalars(stmt).all())


def cancel_session(db: Session, tenant_id: str, operator: Dict, session_id: str) -> ClassSession:
    s = _get_session(db, tenant_id, session_id)
    s.status = 'CANCELLED'
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CANCEL', 'session', s.id)
    return s


def _get_session(db: Session, tenant_id: str, session_id: str) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s or s.tenant_id != tenant_id:
        raise NotFound('课节不存在')
    return s


# ---------- 约课 ----------
def book(db: Session, tenant_id: str, member_id: str, session_id: str,
         source: str = 'MEMBER', operator: Optional[Dict] = None) -> Dict:
    s = _get_session(db, tenant_id, session_id)
    now = now_naive()
    if s.status == 'CANCELLED':
        raise BusinessRuleError('该课程已取消')
    # PRD FR-13 规则③：开课前 N 小时（默认 2h）截止约课，与取消时限同一窗口。
    # 前端据此展示「已截止」并禁用 CTA，此处为服务端的最终校验，二者必须一致。
    booking_deadline = s.starts_at - timedelta(hours=s.cancel_deadline_hours)
    if now >= booking_deadline:
        raise BookingClosed('已超过预约截止时间（开课前 %d 小时）' % s.cancel_deadline_hours)

    # 重复约课
    dup = db.scalar(select(Enrollment).where(
        Enrollment.session_id == session_id, Enrollment.member_id == member_id,
        Enrollment.status.in_(('BOOKED', 'WAITLIST'))))
    if dup:
        raise DuplicateBooking()

    # 爽约限制
    window = now - timedelta(days=NO_SHOW_WINDOW_DAYS)
    no_show = db.scalar(select(func.count(AttendanceRecord.id)).where(
        AttendanceRecord.tenant_id == tenant_id, AttendanceRecord.member_id == member_id,
        AttendanceRecord.status == 'NO_SHOW', AttendanceRecord.created_at >= window)) or 0
    if no_show >= NO_SHOW_LIMIT:
        raise BusinessRuleError('近 %d 天爽约 %d 次，已暂停约课，请联系前台' % (NO_SHOW_WINDOW_DAYS, no_show))

    # 权益预校验
    _check_entitlement(db, tenant_id, member_id)

    if s.booked < s.capacity:
        status, seq = 'BOOKED', 0
        s.booked += 1
        if s.booked >= s.capacity:
            s.status = 'FULL'
    else:
        status = 'WAITLIST'
        max_seq = db.scalar(select(func.max(Enrollment.waitlist_seq)).where(
            Enrollment.session_id == session_id, Enrollment.status == 'WAITLIST')) or 0
        seq = max_seq + 1
        s.status = 'FULL'

    enr = Enrollment(id=random_id(), tenant_id=tenant_id, session_id=session_id,
                     member_id=member_id, store_id=s.store_id, status=status,
                     source=source, waitlist_seq=seq)
    db.add(enr)

    db.add(InAppMessage(id=random_id(), tenant_id=tenant_id, member_id=member_id,
                        title='约课成功' if status == 'BOOKED' else '候补登记成功',
                        content='%s %s' % (s.course_name, s.starts_at.strftime('%m-%d %H:%M')),
                        mtype='BOOKING', ref_id=enr.id))

    if operator:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'BOOK', 'enrollment', enr.id,
                    {'session_id': session_id, 'member_id': member_id, 'status': status})
    return {'id': enr.id, 'status': status, 'waitlist_seq': seq}


def _check_entitlement(db: Session, tenant_id: str, member_id: str) -> None:
    ents = db.scalars(select(Entitlement).where(
        Entitlement.tenant_id == tenant_id, Entitlement.member_id == member_id,
        Entitlement.status == 'ACTIVE')).all()
    for e in ents:
        if e.ptype in ('PACKAGE', 'PRIVATE') and e.used_sessions < e.total_sessions:
            return
        if e.ptype == 'STORED' and e.balance_cents > 0:
            return
    raise BusinessRuleError('暂无可用课时，请先购买课程包')


# ---------- 取消 ----------
def cancel_booking(db: Session, tenant_id: str, member_id: str, enrollment_id: str,
                   operator: Optional[Dict] = None) -> Dict:
    enr = db.get(Enrollment, enrollment_id)
    if not enr or enr.tenant_id != tenant_id:
        raise NotFound('预约记录不存在')
    if enr.member_id != member_id:
        raise NotFound('预约记录不存在')
    if enr.status not in ('BOOKED', 'WAITLIST'):
        raise BusinessRuleError('当前状态不可取消')
    s = _get_session(db, tenant_id, enr.session_id)
    deadline = s.starts_at - timedelta(hours=s.cancel_deadline_hours)
    if now_naive() >= deadline:
        raise BookingClosed('已超过取消时间窗（开课前 %d 小时截止）' % s.cancel_deadline_hours)

    was_booked = enr.status == 'BOOKED'
    enr.status = 'CANCELLED'
    enr.cancelled_at = now_naive()

    if was_booked:
        s.booked = max(0, s.booked - 1)
        if s.status == 'FULL':
            s.status = 'OPEN'
        _promote_waitlist(db, tenant_id, s)

    if operator:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'CANCEL', 'enrollment', enr.id)
    return {'id': enr.id, 'status': 'CANCELLED'}


def _promote_waitlist(db: Session, tenant_id: str, s: ClassSession) -> None:
    first = db.scalar(select(Enrollment).where(
        Enrollment.session_id == s.id, Enrollment.status == 'WAITLIST')
        .order_by(Enrollment.waitlist_seq.asc()).limit(1))
    if not first:
        return
    first.status = 'BOOKED'
    first.waitlist_seq = 0
    s.booked += 1
    if s.booked >= s.capacity:
        s.status = 'FULL'
    # 重排剩余候补序号
    rest = db.scalars(select(Enrollment).where(
        Enrollment.session_id == s.id, Enrollment.status == 'WAITLIST')
        .order_by(Enrollment.waitlist_seq.asc())).all()
    for i, e in enumerate(rest):
        e.waitlist_seq = i + 1
    db.add(InAppMessage(id=random_id(), tenant_id=tenant_id, member_id=first.member_id,
                        title='候补成功转正', content='%s 有空位，已为您确认预约' % s.course_name,
                        mtype='BOOKING', ref_id=first.id))


# ---------- 会员端查询 ----------
def my_bookings(db: Session, tenant_id: str, member_id: str) -> List[Dict]:
    rows = db.scalars(select(Enrollment).where(
        Enrollment.tenant_id == tenant_id, Enrollment.member_id == member_id)
        .order_by(Enrollment.created_at.desc()).limit(50)).all()
    out = []
    for e in rows:
        s = db.get(ClassSession, e.session_id)
        out.append({
            'id': e.id, 'session_id': e.session_id, 'status': e.status,
            'waitlist_seq': e.waitlist_seq,
            'course_name': s.course_name if s else '',
            'starts_at': s.starts_at.strftime('%Y-%m-%d %H:%M') if s else '',
            'store_id': e.store_id,
        })
    return out


def session_detail(db: Session, tenant_id: str, session_id: str, member_id: str = '') -> Dict:
    s = _get_session(db, tenant_id, session_id)
    my_status = ''
    if member_id:
        enr = db.scalar(select(Enrollment).where(
            Enrollment.session_id == session_id, Enrollment.member_id == member_id,
            Enrollment.status.in_(('BOOKED', 'WAITLIST'))))
        my_status = enr.status if enr else ''
    tpl = db.get(CourseTemplate, s.template_id) if s.template_id else None
    if tpl and tpl.tenant_id != tenant_id:
        tpl = None
    coach = db.get(User, s.coach_id) if s.coach_id else None
    store = db.get(Store, s.store_id) if s.store_id else None
    return {
        'id': s.id, 'course_name': s.course_name, 'store_id': s.store_id,
        'coach_id': s.coach_id, 'starts_at': s.starts_at.strftime('%Y-%m-%d %H:%M'),
        'ends_at': s.ends_at.strftime('%H:%M'), 'capacity': s.capacity, 'booked': s.booked,
        'status': s.status, 'cancel_deadline_hours': s.cancel_deadline_hours,
        'my_status': my_status,
        # 供会员端还原定稿课程卡（教练 / 门店 / 课型 / 时长），不虚构教室等模型外字段
        'coach_name': coach.real_name if coach else '',
        'store_name': store.name if store else '',
        'ctype': (tpl.ctype if tpl else 'GROUP'),
        'duration_minutes': (tpl.duration_minutes if tpl else 60),
        'cost_sessions': 1,  # 当前业务口径：一节课消耗 1 课时
    }
