"""排课 / 约课 / 候补 / 取消：约课一致性的核心服务。

约课状态：BOOKED 已约 / WAITLIST 候补 / OFFERED 递补待确认 / CANCELLED / ATTENDED / NO_SHOW。
规则（可配置）：开课前 2h 截止；累计 3 次爽约限约 7 天；候补递补锁定 15 分钟。
权益口径（PRD FR-13/FR-16）：约课前按「卡种匹配 + 课型 + 适用门店/跨店」预校验，
点名时才真正扣课；空规则表示不限制，保证存量权益与历史数据兼容。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import (AppError, BookingClosed, BusinessRuleError, Conflict,
                      DuplicateBooking, EntitlementNotApplicable, NotFound,
                      ValidationError)
from ..models import (AttendanceRecord, ClassSession, CourseTemplate, Enrollment,
                      Entitlement, Store, User)
from ..repositories.base import write_audit
from ..security import random_id
from ..services.notification import notify
from ..utils.timeutil import now_naive, parse_dt

NO_SHOW_LIMIT = 3
NO_SHOW_WINDOW_DAYS = 7

# 入参边界（P0 实测补漏）：课程容量、时长、预约截止窗口、可约卡种白名单
CAPACITY_MIN, CAPACITY_MAX = 1, 200
DURATION_MIN, DURATION_MAX = 15, 300
DEADLINE_HOURS_MAX = 72
PTYPE_ALLOWED = ('PACKAGE', 'PRIVATE', 'STORED')
CTYPE_ALLOWED = ('GROUP', 'PRIVATE')


def _csv(value: str) -> List[str]:
    """逗号分隔规则 → 列表；空串返回空列表（表示不限制）。"""
    return [x.strip() for x in (value or '').split(',') if x.strip()]


def parse_hm(value: str) -> Tuple[int, int]:
    """严格解析 ``HH:MM``，返回 (小时, 分钟)。

    为什么必须校验上下界：``timedelta(hours=25)`` 会静默进位到次日 01:00，
    ``minutes=99`` 会溢出成 10:39 —— 排期表于是出现"看不见的幽灵课节"，
    馆主按周生成 7 天却多出一整天的课。这类数据无法在前端发现，只能在这里拦死。
    """
    text = str(value if value is not None else '').strip()
    parts = text.split(':')
    if len(parts) != 2:
        raise ValidationError('时段必须为 00:00–23:59')
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        raise ValidationError('时段必须为 00:00–23:59')
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValidationError('时段必须为 00:00–23:59')
    return hh, mm


def check_deadline_hours(value) -> int:
    """预约/取消截止小时：开课前 0–72 小时。

    负数会把截止点推到开课之后（开课 3 小时后仍可约课/取消），
    超过 72 小时则等于永久关闭预约，两者都是数据错误，必须在入口拦下。
    """
    try:
        hours = int(value)
    except (TypeError, ValueError):
        raise ValidationError('预约截止小时必须在 0–72 之间')
    if not (0 <= hours <= DEADLINE_HOURS_MAX):
        raise ValidationError('预约截止小时必须在 0–72 之间')
    return hours


def parse_weekly_times(times: List[str]) -> List[Tuple[int, int]]:
    """解析并去重一组 ``HH:MM``。

    去重的必要性：``['9:00', '09:00']`` 解析后是同一时刻，不去重会在一次请求里
    建两节课、也让「跳过数」虚高；路由层与生成逻辑共用同一份解析结果。
    """
    return list(dict.fromkeys(parse_hm(t) for t in times))


def normalize_days(days) -> List[int]:
    """按周生成的「星期」入参：1=周一 … 7=周日，返回去重且升序的列表。

    - 不传（None）= 整周 [1..7]，保持旧调用方与旧客户端的向后兼容；
    - 传空数组视为用户没选任何一天，直接 4xx，而不是静默生成 0 节；
    - 越界/非整数一律 4xx，避免 ``0``（上周日）或 ``8``（下周一）被误当成合法值；
    - **bool / 字符串必须拒**：Python 里 ``True == 1``、``int('1') == 1``，
      不显式拦会让 ``days=[true]`` 或 ``['1']`` 被当成周一，
      把「调用方传错了类型」变成「悄悄少排 4 天」。
    """
    if days is None:
        return list(range(1, 8))
    normalized: List[int] = []
    for raw in days:
        if isinstance(raw, (bool, str)):
            raise ValidationError('请选择 1–7 之间的星期')
        if isinstance(raw, float) and not raw.is_integer():
            raise ValidationError('请选择 1–7 之间的星期')
        try:
            day = int(raw)
        except (TypeError, ValueError):
            raise ValidationError('请选择 1–7 之间的星期')
        if not (1 <= day <= 7):
            raise ValidationError('请选择 1–7 之间的星期')
        if day not in normalized:
            normalized.append(day)
    if not normalized:
        raise ValidationError('请选择 1–7 之间的星期')
    return sorted(normalized)


def _check_capacity(value) -> int:
    try:
        capacity = int(value)
    except (TypeError, ValueError):
        raise ValidationError('课程容量必须在 1–200 之间')
    if not (CAPACITY_MIN <= capacity <= CAPACITY_MAX):
        raise ValidationError('课程容量必须在 1–200 之间')
    return capacity


def _check_duration(value) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError):
        raise ValidationError('课程时长必须在 15–300 分钟之间')
    if not (DURATION_MIN <= duration <= DURATION_MAX):
        raise ValidationError('课程时长必须在 15–300 分钟之间')
    return duration


def _check_ctype(value) -> str:
    ctype = (value or 'GROUP').strip().upper()
    if ctype not in CTYPE_ALLOWED:
        raise ValidationError('课型只能是团课或私教')
    return ctype


def _normalize_ptypes(value) -> str:
    """可约卡种：逗号分隔白名单，空串=不限。"""
    allowed = _csv(value if isinstance(value, str) else ','.join(value or []))
    for p in allowed:
        if p not in PTYPE_ALLOWED:
            raise ValidationError('可约卡种非法：%s' % p)
    # 去重且保持稳定顺序，避免同一含义存成两种字符串形态
    return ','.join(dict.fromkeys(allowed))


# ---------- 课程模板 ----------
def _default_if_blank(value, fallback):
    """只有「没传 / 空串」才落默认值；0 是显式非法输入，必须交给校验器报错。

    旧写法 ``data.get('capacity', 15) or 15`` 会把 capacity=0 静默改成 15，
    库主以为设了 0 人班，实际开了 15 个名额 —— 这类静默纠错比报错危险得多。
    """
    return fallback if value is None or value == '' else value


def create_template(db: Session, tenant_id: str, operator: Dict, data: dict) -> CourseTemplate:
    name = (data.get('name') or '').strip()
    if not name:
        raise ValidationError('课程名称不能为空')
    allowed = _normalize_ptypes(data.get('allowed_ptypes', ''))
    store_id = data.get('store_id', '') or ''
    if store_id:
        store = db.get(Store, store_id)
        if not store or store.tenant_id != tenant_id:
            raise NotFound('门店不存在')
    t = CourseTemplate(id=random_id(), tenant_id=tenant_id,
                       code=data.get('code') or ('C' + random_id()[:6].upper()),
                       name=name, store_id=store_id, coach_id=data.get('coach_id', ''),
                       ctype=_check_ctype(data.get('ctype')),
                       capacity=_check_capacity(_default_if_blank(data.get('capacity'), 15)),
                       duration_minutes=_check_duration(
                           _default_if_blank(data.get('duration_minutes'), 60)),
                       allowed_ptypes=allowed)
    db.add(t)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'course', t.id,
                {'name': name, 'allowed_ptypes': t.allowed_ptypes})
    return t


def get_template(db: Session, tenant_id: str, template_id: str) -> CourseTemplate:
    t = db.get(CourseTemplate, template_id)
    if not t or t.tenant_id != tenant_id:
        raise NotFound('课程模板不存在')
    return t


def update_template(db: Session, tenant_id: str, operator: Dict, template_id: str,
                    data: dict) -> Tuple[CourseTemplate, int]:
    """FR-11：修改课程模板。**只影响之后创建的课节**，存量排期保持快照。

    课节在创建时已把 name/coach/store/capacity 复制成快照，因此改模板不会
    反向改写历史排期（否则已约会员看到的课程信息会凭空变化）；这里额外返回
    未来课节数量，供前端提示"影响 N 节未来课节"。
    """
    t = get_template(db, tenant_id, template_id)
    changes: Dict[str, object] = {}

    if data.get('name') is not None:
        name = str(data.get('name')).strip()
        if not name:
            raise ValidationError('课程名称不能为空')
        t.name = name
        changes['name'] = name
    if data.get('code') is not None:
        code = str(data.get('code')).strip()
        if code and code != t.code:
            exists = db.scalar(select(CourseTemplate).where(
                CourseTemplate.tenant_id == tenant_id, CourseTemplate.code == code,
                CourseTemplate.id != t.id))
            if exists:
                raise Conflict('课程编码已存在')
            t.code = code
            changes['code'] = code
    if data.get('ctype') is not None:
        t.ctype = _check_ctype(data.get('ctype'))
        changes['ctype'] = t.ctype
    if data.get('capacity') is not None and data.get('capacity') != '':
        t.capacity = _check_capacity(data.get('capacity'))
        changes['capacity'] = t.capacity
    if data.get('duration_minutes') is not None and data.get('duration_minutes') != '':
        t.duration_minutes = _check_duration(data.get('duration_minutes'))
        changes['duration_minutes'] = t.duration_minutes
    if data.get('store_id') is not None:
        store_id = str(data.get('store_id') or '')
        if store_id:
            store = db.get(Store, store_id)
            if not store or store.tenant_id != tenant_id:
                raise NotFound('门店不存在')
            # 不能把模板挪进已停用门店（等于变相给它排课）；
            # 门店没变时放行，否则馆主连改模板名字都会被 422 挡住。
            if store.status != 'ACTIVE' and store_id != t.store_id:
                raise BusinessRuleError('该门店已停用，不能排课')
        t.store_id = store_id
        changes['store_id'] = store_id
    if data.get('coach_id') is not None:
        t.coach_id = str(data.get('coach_id') or '')
        changes['coach_id'] = t.coach_id
    if data.get('allowed_ptypes') is not None:
        t.allowed_ptypes = _normalize_ptypes(data.get('allowed_ptypes'))
        changes['allowed_ptypes'] = t.allowed_ptypes

    affected = int(db.scalar(select(func.count(ClassSession.id)).where(
        ClassSession.tenant_id == tenant_id,
        ClassSession.template_id == t.id,
        ClassSession.starts_at > now_naive())) or 0)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'UPDATE', 'course', t.id, changes)
    return t, affected


def list_templates(db: Session, tenant_id: str, store_id: str = '') -> List[CourseTemplate]:
    stmt = select(CourseTemplate).where(CourseTemplate.tenant_id == tenant_id)
    if store_id:
        stmt = stmt.where(CourseTemplate.store_id == store_id)
    return list(db.scalars(stmt).all())


# ---------- 排期 ----------
def ensure_store_active(db: Session, tenant_id: str, store_id: str) -> None:
    """门店已停用时禁止新增排课。

    停用门店的语义是「不再对外经营」，若还能往里排课，会员端会继续放课、
    馆主却以为店已经关了；这里在排期入口统一拦截（门店为空视为不限门店，放行）。
    """
    if not store_id:
        return
    store = db.get(Store, store_id)
    if not store or store.tenant_id != tenant_id:
        raise NotFound('门店不存在')
    if store.status != 'ACTIVE':
        raise BusinessRuleError('该门店已停用，不能新增排课')


def create_session(db: Session, tenant_id: str, operator: Dict, data: dict) -> ClassSession:
    template = db.get(CourseTemplate, data.get('template_id', ''))
    if not template or template.tenant_id != tenant_id:
        raise NotFound('课程模板不存在')
    ensure_store_active(db, tenant_id, template.store_id)
    starts_at = parse_dt(data['starts_at'])
    deadline_hours = check_deadline_hours(data.get('cancel_deadline_hours', 2))
    ends_at = starts_at + timedelta(minutes=template.duration_minutes)
    s = ClassSession(id=random_id(), tenant_id=tenant_id, template_id=template.id,
                     store_id=template.store_id, coach_id=template.coach_id,
                     course_name=template.name, starts_at=starts_at, ends_at=ends_at,
                     capacity=template.capacity,
                     cancel_deadline_hours=deadline_hours)
    db.add(s)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'session', s.id,
                {'course': template.name, 'starts_at': starts_at.strftime('%Y-%m-%d %H:%M')})
    return s


def normalize_week_start(week_start: str) -> datetime:
    """把任意日期吸附到它所在周的周一（``weekday()`` 周一=0）。

    G6：按周生成的偏移是「周一 + (星期-1)」，一旦入参不是周一，勾「周一三五」
    就会落到「周三五日」——静默错位比报错更难发现。这里统一吸附到周一，
    幂等判断也用吸附后的日期，避免同一周换个非周一入参就重复建课。
    """
    try:
        base = datetime.strptime((week_start or '')[:10], '%Y-%m-%d')
    except ValueError:
        raise ValidationError('week_start 需为 YYYY-MM-DD')
    return base - timedelta(days=base.weekday())


def generate_weekly_sessions(db: Session, tenant_id: str, operator: Dict, template_id: str,
                             week_start: str, times: List[str],
                             cancel_deadline_hours: int = 2,
                             days: Optional[List[int]] = None) -> List[ClassSession]:
    """FR-11：按周批量生成排期。幂等——同一模板 + 同一起始时间已存在则跳过。

    ``days`` 为 1=周一 … 7=周日；不传=整周（兼容旧客户端）。
    ``week_start`` 允许传任意日期，内部会吸附到该周周一（见 ``normalize_week_start``）。
    """
    template = db.get(CourseTemplate, template_id)
    if not template or template.tenant_id != tenant_id:
        raise NotFound('课程模板不存在')
    ensure_store_active(db, tenant_id, template.store_id)
    if not times:
        raise ValidationError('至少提供一个上课时段')
    effective_base = normalize_week_start(week_start)
    # 先做全量校验再落库：时段/星期/截止小时任一非法则整批拒绝，不留半批脏数据
    parsed_times = parse_weekly_times(times)
    day_indexes = normalize_days(days)
    deadline_hours = check_deadline_hours(cancel_deadline_hours)
    created: List[ClassSession] = []
    for day in day_indexes:
        for hh, mm in parsed_times:
            starts_at = effective_base + timedelta(days=day - 1, hours=hh, minutes=mm)
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
                             cancel_deadline_hours=deadline_hours)
            db.add(s)
            created.append(s)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'GENERATE_WEEKLY', 'session',
                template_id, {'requested_week_start': (week_start or '')[:10],
                              'effective_week_start': effective_base.strftime('%Y-%m-%d'),
                              'days': day_indexes, 'created': len(created)})
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
# 占位状态：BOOKED/OFFERED 都实际占一个名额（候补锁定窗口内名额已被预留）
SEAT_STATUSES = ('BOOKED', 'OFFERED')


def recount_session_booked(db: Session, tenant_id: str, session_id: str) -> int:
    """用一条 UPDATE 子查询把 ``booked`` 重算为 BOOKED+OFFERED 的 COUNT，并同步 FULL/OPEN。

    为什么不用 ``booked ± 1``：并发下 +1/-1 会丢更新，20 次取消递补就能累积出漂移。
    为什么先 ``flush``：本会话是 ``autoflush=False``，不落盘子查询看不到刚改的状态。
    为什么 ``expire_all``：UPDATE 走 Core 表达式，identity map 里的旧值必须失效。
    """
    db.flush()
    active_subq = (
        select(func.count(Enrollment.id))
        .where(Enrollment.session_id == session_id,
               Enrollment.status.in_(SEAT_STATUSES))
        .scalar_subquery()
    )
    stmt = (
        update(ClassSession)
        .where(ClassSession.id == session_id, ClassSession.tenant_id == tenant_id)
        .values(
            booked=active_subq,
            status=case(
                # 已停课/已结束/已截止的课节不覆盖人工状态
                (ClassSession.status.in_(('CANCELLED', 'FINISHED', 'CLOSED')),
                 ClassSession.status),
                (active_subq >= ClassSession.capacity, 'FULL'),
                else_='OPEN',
            ),
        )
        .execution_options(synchronize_session=False)
    )
    db.execute(stmt)
    db.expire_all()
    current = db.scalar(select(ClassSession.booked).where(ClassSession.id == session_id))
    return int(current or 0)


def _try_take_seat(db: Session, tenant_id: str, session_id: str) -> bool:
    """单语句原子占位：命中即占到，rowcount==0 表示名额已满或课节不可约。

    禁止 ``with_for_update()``：SQLite 会静默忽略它（等于没加锁），
    且 MySQL 上行锁持有期间还会执行同步 HTTP 通知，放大死锁与超时风险。
    """
    stmt = (
        update(ClassSession)
        .where(ClassSession.id == session_id,
               ClassSession.tenant_id == tenant_id,
               ClassSession.status != 'CANCELLED',
               ClassSession.booked < ClassSession.capacity)
        .values(
            booked=ClassSession.booked + 1,
            status=case((ClassSession.booked + 1 >= ClassSession.capacity, 'FULL'),
                        else_=ClassSession.status),
        )
        .execution_options(synchronize_session=False)
    )
    return db.execute(stmt).rowcount == 1


def _take_seat(db: Session, tenant_id: str, session_id: str, session: ClassSession) -> str:
    """返回 BOOKED 或 WAITLIST。占位失败先判定是否真满，再重试一次。"""
    if _try_take_seat(db, tenant_id, session_id):
        return 'BOOKED'
    # 条件 UPDATE 未命中：可能是真满员，也可能是并发窗口内的瞬时状态，必须重读判定
    db.expire(session)
    if int(session.booked) >= int(session.capacity):
        return 'WAITLIST'
    if _try_take_seat(db, tenant_id, session_id):
        return 'BOOKED'
    db.expire(session)
    if int(session.booked) >= int(session.capacity):
        return 'WAITLIST'
    raise Conflict('课节名额状态冲突，请稍后重试')


def _write_enrollment(db: Session, tenant_id: str, member_id: str, session: ClassSession,
                      status: str, source: str, reuse_id: str = '') -> Tuple[str, int]:
    """落预约记录：新建，或复用本会员在该课节的历史 CANCELLED 行。

    为什么复用而不是直接 INSERT：``uq_enroll_session_member`` 是
    (session_id, member_id) **全状态唯一**，取消记录仍占着这个唯一键，
    直接 INSERT 会被约束打回 409 —— 会员取消后就永远约不上同一节课了。
    复用只改状态与候补序号，不动主键与 created_at，跨库行为一致且不需要改索引。

    候补序号统一用单语句 ``COALESCE(MAX(seq),0)+1`` 取号，避免并列。
    同会员并发重复约由唯一约束兜底，统一转成 409 DuplicateBooking。
    """
    enrollment_id = reuse_id or random_id()
    if status == 'WAITLIST':
        seq_value = (
            select(func.coalesce(func.max(Enrollment.waitlist_seq), 0) + 1)
            .where(Enrollment.session_id == session.id,
                   Enrollment.status == 'WAITLIST')
            .scalar_subquery()
        )
    else:
        seq_value = 0
    try:
        if reuse_id:
            # 复用时先 flush 会话内既有变更，再原子改状态（顺序不能换）
            db.flush()
            stmt = (
                update(Enrollment)
                .where(Enrollment.id == reuse_id,
                       Enrollment.tenant_id == tenant_id)
                .values(status=status, source=source, store_id=session.store_id,
                        waitlist_seq=seq_value, cancelled_at=None,
                        offer_expires_at=None, updated_at=now_naive())
                .execution_options(synchronize_session=False)
            )
        else:
            stmt = insert(Enrollment).values(
                id=enrollment_id, tenant_id=tenant_id, session_id=session.id,
                member_id=member_id, store_id=session.store_id, status=status,
                source=source, waitlist_seq=seq_value, cancelled_at=None,
                offer_expires_at=None,
            )
        db.execute(stmt)
        db.flush()
    except IntegrityError as exc:
        # 同会员同课节的唯一约束（含并发重复提交）；回滚后由调用方统一转 409
        db.rollback()
        raise DuplicateBooking('该课节已存在预约记录，请勿重复提交') from exc
    seq = 0
    if status == 'WAITLIST':
        seq = int(db.scalar(select(Enrollment.waitlist_seq).where(
            Enrollment.id == enrollment_id)) or 0)
    if reuse_id:
        # Core UPDATE 绕过了 ORM，让 identity map 里的旧状态失效
        db.expire_all()
    return enrollment_id, seq


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

    # 同一课节的历史预约：有效状态拦截；已取消则复用该行（唯一约束是全状态唯一）
    prior = db.scalar(select(Enrollment).where(
        Enrollment.session_id == session_id, Enrollment.member_id == member_id))
    reuse_id = ''
    if prior is not None:
        if prior.status in ('BOOKED', 'WAITLIST', 'OFFERED'):
            raise DuplicateBooking()
        if prior.status in ('ATTENDED', 'NO_SHOW'):
            raise DuplicateBooking('该课节已点名处理，不可重复预约')
        reuse_id = prior.id  # CANCELLED：占位成功后把这条记录改回有效状态

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

    status = _take_seat(db, tenant_id, session_id, s)
    enr_id, seq = _write_enrollment(db, tenant_id, member_id, s, status, source, reuse_id)
    # 占位后必须让 identity map 失效，否则同一请求内回显的 booked 是旧值
    db.expire(s)

    notify(db, tenant_id, member_id, '约课成功' if status == 'BOOKED' else '候补登记成功',
           '%s %s' % (s.course_name, s.starts_at.strftime('%m-%d %H:%M')),
           'BOOKING', ref_id=enr_id)

    if operator:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'BOOK', 'enrollment', enr_id,
                    {'session_id': session_id, 'member_id': member_id, 'status': status})
    return {'id': enr_id, 'status': status, 'waitlist_seq': seq}


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
        # 名额不再 ±1，统一走 COUNT 重算，避免并发丢更新累积漂移
        _promote_waitlist(db, tenant_id, s)

    if operator:
        write_audit(db, tenant_id, operator['id'], operator['name'], 'CANCEL', 'enrollment', enr.id)
    return {'id': enr.id, 'status': 'CANCELLED'}


# ---------- 候补递补（FR-14：15 分钟确认锁定） ----------
def _promote_waitlist(db: Session, tenant_id: str, s: ClassSession) -> None:
    """顺延下一位候补并重算名额（唯一入口，保证任何分支都刷新 booked）。"""
    now = now_naive()
    active_offer = db.scalar(select(Enrollment).where(
        Enrollment.session_id == s.id, Enrollment.status == 'OFFERED',
        Enrollment.offer_expires_at > now))
    first = None
    if not active_offer:
        # 已有会员在锁定窗口内确认，暂不递补下一位
        first = db.scalar(select(Enrollment).where(
            Enrollment.session_id == s.id, Enrollment.status == 'WAITLIST')
            .order_by(Enrollment.waitlist_seq.asc(), Enrollment.created_at.asc()).limit(1))
    if first is not None:
        first.status = 'OFFERED'
        first.waitlist_seq = 0
        first.offer_expires_at = now + timedelta(minutes=settings.WAITLIST_OFFER_MINUTES)
        db.flush()
        rest = db.scalars(select(Enrollment).where(
            Enrollment.session_id == s.id, Enrollment.status == 'WAITLIST')
            .order_by(Enrollment.waitlist_seq.asc(), Enrollment.created_at.asc())).all()
        for i, e in enumerate(rest, start=1):
            e.waitlist_seq = i
        notify(db, tenant_id, first.member_id, '候补递补待确认',
               '%s 有空位，请在 %d 分钟内确认，逾期将顺延下一位'
               % (s.course_name, settings.WAITLIST_OFFER_MINUTES),
               'WAITLIST_OFFER', ref_id=first.id)
    recount_session_booked(db, tenant_id, s.id)


def expire_offers(db: Session, tenant_id: str, session_id: str = '') -> int:
    """惰性清理过期递补：退回候补队尾、释放名额、通知本人、顺延下一位。"""
    now = now_naive()
    stmt = select(Enrollment).where(
        Enrollment.tenant_id == tenant_id, Enrollment.status == 'OFFERED',
        Enrollment.offer_expires_at.is_not(None),
        Enrollment.offer_expires_at <= now)
    if session_id:
        stmt = stmt.where(Enrollment.session_id == session_id)
    expired = list(db.scalars(stmt).all())
    touched: List[str] = []
    for e in expired:
        base = db.scalar(select(func.coalesce(func.max(Enrollment.waitlist_seq), 0)).where(
            Enrollment.session_id == e.session_id,
            Enrollment.status == 'WAITLIST')) or 0
        e.status = 'WAITLIST'
        e.waitlist_seq = int(base) + 1
        e.offer_expires_at = None
        # 逐条落盘：让同一批次的下一条看到递增后的最大序号，避免并列
        db.flush()
        if e.session_id not in touched:
            touched.append(e.session_id)
        notify(db, tenant_id, e.member_id, '候补递补已超时',
               '未在规定时间内确认，名额已顺延下一位', 'WAITLIST_EXPIRED', ref_id=e.id)
    for sid in touched:
        s = db.get(ClassSession, sid)
        if s and s.tenant_id == tenant_id:
            _promote_waitlist(db, tenant_id, s)
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
    db.flush()
    recount_session_booked(db, tenant_id, e.session_id)
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
