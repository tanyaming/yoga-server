"""排课 / 约课 / 候补 / 取消：约课一致性的核心服务。

约课状态：BOOKED 已约 / WAITLIST 候补 / OFFERED 递补待确认 / CANCELLED / ATTENDED / NO_SHOW。
规则（可配置）：开课前 2h 截止；累计 3 次爽约限约 7 天；候补递补锁定 15 分钟。
权益口径（PRD FR-13/FR-16）：约课前按「卡种匹配 + 课型 + 适用门店/跨店」预校验，
点名时才真正扣课；空规则表示不限制，保证存量权益与历史数据兼容。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import (AppError, BookingClosed, BusinessRuleError, DuplicateBooking,
                      EntitlementNotApplicable, NotFound, ValidationError)
from ..models import (AttendanceRecord, ClassSession, CourseTemplate, Enrollment,
                      Entitlement, Store, User)
from ..repositories.base import write_audit
from ..security import random_id
from ..services.notification import notify
from ..utils.timeutil import now_naive, parse_dt

NO_SHOW_LIMIT = 3
NO_SHOW_WINDOW_DAYS = 7


def _csv(value: str) -> List[str]:
    """逗号分隔规则 → 列表；空串返回空列表（表示不限制）。"""
    return [x.strip() for x in (value or '').split(',') if x.strip()]


# ---------- 课程模板 ----------
def create_template(db: Session, tenant_id: str, operator: Dict, data: dict) -> CourseTemplate:
    name = (data.get('name') or '').strip()
    if not name:
        raise ValidationError('课程名称不能为空')
    allowed = _csv(data.get('allowed_ptypes', ''))
    for p in allowed:
        if p not in ('PACKAGE', 'PRIVATE', 'STORED'):
            raise ValidationError('可约卡种非法：%s' % p)
    t = CourseTemplate(id=random_id(), tenant_id=tenant_id,
                       code=data.get('code') or ('C' + random_id()[:6].upper()),
                       name=name, store_id=data.get('store_id', ''), coach_id=data.get('coach_id', ''),
                       ctype=data.get('ctype') or 'GROUP',
                       capacity=int(data.get('capacity', 15) or 15),
                       duration_minutes=int(data.get('duration_minutes', 60) or 60),
                       allowed_ptypes=','.join(allowed))
    db.add(t)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'course', t.id,
                {'name': name, 'allowed_ptypes': t.allowed_ptypes})
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


def generate_weekly_sessions(db: Session, tenant_id: str, operator: Dict, template_id: str,
                             week_start: str, times: List[str],
                             cancel_deadline_hours: int = 2) -> List[ClassSession]:
    """FR-11：按周批量生成排期。幂等——同一模板 + 同一起始时间已存在则跳过。"""
    template = db.get(CourseTemplate, template_id)
    if not template or template.tenant_id != tenant_id:
        raise NotFound('课程模板不存在')
    if not times:
        raise ValidationError('至少提供一个上课时段')
    try:
        base = datetime.strptime(week_start[:10], '%Y-%m-%d')
    except ValueError:
        raise ValidationError('week_start 需为 YYYY-MM-DD')
    created: List[ClassSession] = []
    for day in range(7):
        for t in times:
            try:
                hh, mm = [int(x) for x in t.split(':')[:2]]
            except (ValueError, AttributeError):
                raise ValidationError('时段格式需为 HH:MM')
            starts_at = base + timedelta(days=day, hours=hh, minutes=mm)
            exists = db.scalar(select(ClassSession).where(
                ClassSession.tenant_id == tenant_id,
                ClassSession.template_id == template_id,
                ClassSession.starts_at == starts_at))
            if exists:
                continue
            s = ClassSession(id=random_id(), tenant_id=tenant_id, template_id=template_id,
                             store_id=template.store_id, coach_id=template.coach_id,
                             course_name=template.name, starts_at=starts_at,
                             ends_at=starts_at + timedelta(minutes=template.duration_minutes),
                             capacity=template.capacity,
                             cancel_deadline_hours=int(cancel_deadline_hours or 2))
            db.add(s)
            created.append(s)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'GENERATE_WEEKLY', 'session',
                template_id, {'week_start': week_start[:10], 'created': len(created)})
    return created


def list_sessions(db: Session, tenant_id: str, store_id: str = '', date: str = '',
                  coach_id: str = '', since=None) -> List[ClassSession]:
    """排期查询。

    date 有值时按单日精确查询（会员主动切换日期、后台核对历史均可用）；
    since 为 datetime 时限制时间下界，用于会员端默认「今天及未来」。
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
    """FR-11 / FR-12：停课后自动通知全部有效预约会员。"""
    s = _get_session(db, tenant_id, session_id)
    if s.status == 'CANCELLED':
        raise BusinessRuleError('该课程已取消')
    s.status = 'CANCELLED'
    enrs = db.scalars(select(Enrollment).where(
        Enrollment.tenant_id == tenant_id, Enrollment.session_id == s.id,
        Enrollment.status.in_(('BOOKED', 'WAITLIST', 'OFFERED')))).all()
    for e in enrs:
        notify(db, tenant_id, e.member_id, '课程停课通知',
               '%s 已停课，如需上课请重新预约' % s.course_name,
               'SESSION_CANCEL', ref_id=s.id)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CANCEL', 'session', s.id,
                {'notified': len(enrs)})
    return s


def _get_session(db: Session, tenant_id: str, session_id: str) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s or s.tenant_id != tenant_id:
        raise NotFound('课节不存在')
    return s


# ---------- 约课 ----------
def book(db: Session, tenant_id: str, member_id: str, session_id: str,
         source: str = 'MEMBER', operator: Optional[Dict] = None) -> Dict:
    expire_offers(db, tenant_id, session_id)
    s = _get_session(db, tenant_id, session_id)
    now = now_naive()
    if s.status == 'CANCELLED':
        raise BusinessRuleError('该课程已取消')
    # FR-13 规则③：开课前 N 小时（默认 2h）截止，与取消时限同一窗口。
    booking_deadline = s.starts_at - timedelta(hours=s.cancel_deadline_hours)
    if now >= booking_deadline:
        raise BookingClosed('已超过预约截止时间（开课前 %d 小时）' % s.cancel_deadline_hours)

    # 重复约课（同一课节）
    dup = db.scalar(select(Enrollment).where(
        Enrollment.session_id == session_id, Enrollment.member_id == member_id,
        Enrollment.status.in_(('BOOKED', 'WAITLIST', 'OFFERED'))))
    if dup:
        raise DuplicateBooking()

    # FR-13 规则④：同一时段不可重复约课，跨店同样拦截（只比对未来的有效预约）
    clash = db.scalar(
        select(Enrollment).join(ClassSession, Enrollment.session_id == ClassSession.id).where(
            Enrollment.tenant_id == tenant_id,
            Enrollment.member_id == member_id,
            Enrollment.status.in_(('BOOKED', 'OFFERED')),
            Enrollment.session_id != session_id,
            ClassSession.status != 'CANCELLED',
            ClassSession.starts_at > now,
            ClassSession.starts_at < s.ends_at,
            ClassSession.ends_at > s.starts_at))
    if clash:
        raise DuplicateBooking('同一时段已有其他课程预约')

    # 爽约限制
    window = now - timedelta(days=NO_SHOW_WINDOW_DAYS)
    no_show = db.scalar(select(func.count(AttendanceRecord.id)).where(
        AttendanceRecord.tenant_id == tenant_id, AttendanceRecord.member_id == member_id,
        AttendanceRecord.status == 'NO_SHOW', AttendanceRecord.created_at >= window)) or 0
    if no_show >= NO_SHOW_LIMIT:
        raise BusinessRuleError('近 %d 天爽约 %d 次，已暂停约课，请联系前台' % (NO_SHOW_WINDOW_DAYS, no_show))

    # FR-13① / FR-16：卡种匹配、课型与适用门店预校验
    _check_entitlement(db, tenant_id, member_id, s)

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

    notify(db, tenant_id, member_id, '约课成功' if status == 'BOOKED' else '候补登记成功',
           '%s %s' % (s.course_name, s.starts_at.strftime('%m-%d %H:%M')),
           'BOOKING', ref_id=enr.id)

    if operator:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'BOOK', 'enrollment', enr.id,
                    {'session_id': session_id, 'member_id': member_id, 'status': status})
    return {'id': enr.id, 'status': status, 'waitlist_seq': seq}


def _check_entitlement(db: Session, tenant_id: str, member_id: str,
                       s: ClassSession) -> None:
    """FR-13①/FR-16：按卡种、课型、适用门店筛选可用权益。

    规则为空表示不限制（存量权益 / 旧商品默认全通用），避免升级后历史会员无法约课。
    没有任何适用权益时区分「完全无权益」与「权益不适用」两种错误语义。
    """
    tpl = db.get(CourseTemplate, s.template_id) if s.template_id else None
    if tpl and tpl.tenant_id != tenant_id:
        tpl = None
    allowed_ptypes = _csv(tpl.allowed_ptypes) if tpl else []
    ctype = tpl.ctype if tpl else 'GROUP'
    ents = db.scalars(select(Entitlement).where(
        Entitlement.tenant_id == tenant_id, Entitlement.member_id == member_id,
        Entitlement.status == 'ACTIVE')).all()
    reasons: List[str] = []
    for e in ents:
        usable = ((e.ptype in ('PACKAGE', 'PRIVATE') and e.used_sessions < e.total_sessions)
                  or (e.ptype == 'STORED' and e.balance_cents > 0))
        if not usable:
            continue
        if allowed_ptypes and e.ptype not in allowed_ptypes:
            reasons.append('当前卡种不适用于该课程')
            continue
        if e.allowed_ctypes and ctype not in _csv(e.allowed_ctypes):
            reasons.append('权益不适用于该课型')
            continue
        if (not e.cross_store and e.applicable_store_ids
                and s.store_id not in _csv(e.applicable_store_ids)):
            reasons.append('权益不适用于该门店，且该卡不可跨店使用')
            continue
        return
    if not ents:
        raise BusinessRuleError('暂无可用课时，请先购买课程包')
    if reasons:
        raise EntitlementNotApplicable(reasons[0])
    raise BusinessRuleError('暂无可用课时，请先购买课程包')


def entitlement_applicability(db: Session, tenant_id: str, member_id: str,
                              s: ClassSession) -> Dict:
    """供会员端课程卡置灰使用：返回是否可约及原因，不改变任何数据。"""
    try:
        _check_entitlement(db, tenant_id, member_id, s)
        return {'applicable': True, 'not_applicable_reason': ''}
    except AppError as ex:
        return {'applicable': False, 'not_applicable_reason': ex.message}


# ---------- 取消 ----------
def cancel_booking(db: Session, tenant_id: str, member_id: str, enrollment_id: str,
                   operator: Optional[Dict] = None) -> Dict:
    enr = db.get(Enrollment, enrollment_id)
    if not enr or enr.tenant_id != tenant_id:
        raise NotFound('预约记录不存在')
    if enr.member_id != member_id:
        raise NotFound('预约记录不存在')
    if enr.status not in ('BOOKED', 'WAITLIST', 'OFFERED'):
        raise BusinessRuleError('当前状态不可取消')
    s = _get_session(db, tenant_id, enr.session_id)
    deadline = s.starts_at - timedelta(hours=s.cancel_deadline_hours)
    if now_naive() >= deadline:
        raise BookingClosed('已超过取消时间窗（开课前 %d 小时截止）' % s.cancel_deadline_hours)

    was_booked = enr.status in ('BOOKED', 'OFFERED')
    enr.status = 'CANCELLED'
    enr.cancelled_at = now_naive()
    enr.offer_expires_at = None

    if was_booked:
        s.booked = max(0, s.booked - 1)
        if s.status == 'FULL':
            s.status = 'OPEN'
        _promote_waitlist(db, tenant_id, s)

    if operator:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'CANCEL', 'enrollment', enr.id)
    return {'id': enr.id, 'status': 'CANCELLED'}


# ---------- 候补递补（FR-14：15 分钟确认锁定） ----------
def _promote_waitlist(db: Session, tenant_id: str, s: ClassSession) -> None:
    now = now_naive()
    active_offer = db.scalar(select(Enrollment).where(
        Enrollment.session_id == s.id, Enrollment.status == 'OFFERED',
        Enrollment.offer_expires_at > now))
    if active_offer:
        return  # 已有会员在锁定窗口内确认，暂不递补下一位
    first = db.scalar(select(Enrollment).where(
        Enrollment.session_id == s.id, Enrollment.status == 'WAITLIST')
        .order_by(Enrollment.waitlist_seq.asc()).limit(1))
    if not first:
        return
    first.status = 'OFFERED'
    first.waitlist_seq = 0
    first.offer_expires_at = now + timedelta(minutes=settings.WAITLIST_OFFER_MINUTES)
    s.booked += 1
    if s.booked >= s.capacity:
        s.status = 'FULL'
    rest = db.scalars(select(Enrollment).where(
        Enrollment.session_id == s.id, Enrollment.status == 'WAITLIST')
        .order_by(Enrollment.waitlist_seq.asc())).all()
    for i, e in enumerate(rest):
        e.waitlist_seq = i + 1
    notify(db, tenant_id, first.member_id, '候补递补待确认',
           '%s 有空位，请在 %d 分钟内确认，逾期将顺延下一位'
           % (s.course_name, settings.WAITLIST_OFFER_MINUTES),
           'WAITLIST_OFFER', ref_id=first.id)


def expire_offers(db: Session, tenant_id: str, session_id: str = '') -> int:
    """惰性清理过期递补：退回候补队尾、释放名额、通知本人、顺延下一位。"""
    now = now_naive()
    stmt = select(Enrollment).where(
        Enrollment.tenant_id == tenant_id, Enrollment.status == 'OFFERED',
        Enrollment.offer_expires_at <= now)
    if session_id:
        stmt = stmt.where(Enrollment.session_id == session_id)
    expired = list(db.scalars(stmt).all())
    for e in expired:
        max_seq = db.scalar(select(func.max(Enrollment.waitlist_seq)).where(
            Enrollment.session_id == e.session_id,
            Enrollment.status == 'WAITLIST')) or 0
        e.status = 'WAITLIST'
        e.waitlist_seq = max_seq + 1
        e.offer_expires_at = None
        s = db.get(ClassSession, e.session_id)
        if s and s.tenant_id == tenant_id:
            s.booked = max(0, s.booked - 1)
            if s.status == 'FULL':
                s.status = 'OPEN'
            _promote_waitlist(db, tenant_id, s)
        notify(db, tenant_id, e.member_id, '候补递补已超时',
               '未在规定时间内确认，名额已顺延下一位', 'WAITLIST_EXPIRED', ref_id=e.id)
    return len(expired)


def confirm_offer(db: Session, tenant_id: str, member_id: str, enrollment_id: str) -> Dict:
    """会员确认递补名额：OFFERED → BOOKED，超时则先清理再明确报错。"""
    expire_offers(db, tenant_id)
    e = db.get(Enrollment, enrollment_id)
    if not e or e.tenant_id != tenant_id or e.member_id != member_id:
        raise NotFound('预约记录不存在')
    if e.status != 'OFFERED':
        raise BusinessRuleError('当前没有待确认的递补名额（可能已超时顺延）')
    e.status = 'BOOKED'
    e.waitlist_seq = 0
    e.offer_expires_at = None
    notify(db, tenant_id, member_id, '递补确认成功', '名额已为您锁定，请按时上课',
           'WAITLIST_PROMOTED', ref_id=e.id)
    return {'id': e.id, 'status': 'BOOKED'}


# ---------- 会员端查询 ----------
def my_bookings(db: Session, tenant_id: str, member_id: str) -> List[Dict]:
    expire_offers(db, tenant_id)
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
            'can_confirm': e.status == 'OFFERED',
            'can_review': e.status == 'ATTENDED',
            'offer_expires_at': (e.offer_expires_at.strftime('%Y-%m-%d %H:%M')
                                 if e.offer_expires_at else ''),
        })
    return out


def session_detail(db: Session, tenant_id: str, session_id: str, member_id: str = '') -> Dict:
    s = _get_session(db, tenant_id, session_id)
    my_status = ''
    if member_id:
        enr = db.scalar(select(Enrollment).where(
            Enrollment.session_id == session_id, Enrollment.member_id == member_id,
            Enrollment.status.in_(('BOOKED', 'WAITLIST', 'OFFERED'))))
        my_status = enr.status if enr else ''
    tpl = db.get(CourseTemplate, s.template_id) if s.template_id else None
    if tpl and tpl.tenant_id != tenant_id:
        tpl = None
    coach = db.get(User, s.coach_id) if s.coach_id else None
    store = db.get(Store, s.store_id) if s.store_id else None
    out = {
        'id': s.id, 'course_name': s.course_name, 'store_id': s.store_id,
        'coach_id': s.coach_id, 'starts_at': s.starts_at.strftime('%Y-%m-%d %H:%M'),
        'ends_at': s.ends_at.strftime('%H:%M'), 'capacity': s.capacity, 'booked': s.booked,
        'status': s.status, 'cancel_deadline_hours': s.cancel_deadline_hours,
        'my_status': my_status,
        # 供会员端还原定稿课程卡（教练 / 门店 / 课型 / 时长），不虚构模型外字段
        'coach_name': coach.real_name if coach else '',
        'store_name': store.name if store else '',
        'ctype': (tpl.ctype if tpl else 'GROUP'),
        'duration_minutes': (tpl.duration_minutes if tpl else 60),
        'cost_sessions': 1,  # 当前业务口径：一节课消耗 1 课时
        'allowed_ptypes': (tpl.allowed_ptypes if tpl else ''),
    }
    if member_id:
        out.update(entitlement_applicability(db, tenant_id, member_id, s))
    else:
        out.update({'applicable': True, 'not_applicable_reason': ''})
    return out
