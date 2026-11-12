"""超员脏数据修复：把历史上因并发超卖写坏的课节名额修回合法状态。

背景：约课曾用「读-改-写」维护 ``class_sessions.booked``，容量=1 的课节在 5 并发下
能产出 4 条 BOOKED、而 ``booked`` 只等于 1。迁移与 ``scripts/repair_booked.py``
共用同一套判定逻辑：

1. 超员 BOOKED/OFFERED 按 ``created_at ASC`` 每课节保留前 capacity 条；
2. 其余降级为 WAITLIST，并接着现有 WAITLIST 最大序号重排 ``waitlist_seq``；
3. 按 ``COUNT(BOOKED/OFFERED)`` 重算 ``booked`` 并同步 FULL/OPEN；
4. 每条降级写一条 ``AuditLog(action='DATA_REPAIR')``，便于向会员解释与事后追溯。

降级而不是删除：会员的预约不能凭空消失（R8 风险项），退到候补队列由业务自行决定去留。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuditLog, ClassSession, Enrollment, Tenant
from ..security import random_id

ACTIVE_SEAT_STATUSES = ('BOOKED', 'OFFERED')
DATA_REPAIR_ACTION = 'DATA_REPAIR'


@dataclass
class SessionRepairPlan:
    """单个课节的修复计划（dry-run 与 apply 共用同一份结构）。"""

    session_id: str
    tenant_id: str
    course_name: str
    capacity: int
    booked_before: int
    active_before: int
    keep_ids: List[str] = field(default_factory=list)
    demote_ids: List[str] = field(default_factory=list)
    demote_members: List[str] = field(default_factory=list)
    waitlist_max_seq: int = 0

    @property
    def over(self) -> int:
        return max(0, self.active_before - self.capacity)

    @property
    def needs_fix(self) -> bool:
        return self.over > 0 or self.booked_before != self.active_before

    def as_row(self) -> Dict:
        return {
            'session_id': self.session_id,
            'tenant_id': self.tenant_id,
            'course_name': self.course_name,
            'capacity': self.capacity,
            'booked_before': self.booked_before,
            'active_before': self.active_before,
            'over': self.over,
            'demote_count': len(self.demote_ids),
            'booked_after': self.active_before - len(self.demote_ids),
            'needs_fix': self.needs_fix,
        }


def build_repair_plan(db: Session, tenant_id: str = '') -> List[SessionRepairPlan]:
    """只读扫描：返回需要修复的课节清单（含 booked 与实际占位不一致的漂移）。"""
    stmt = select(ClassSession)
    if tenant_id:
        stmt = stmt.where(ClassSession.tenant_id == tenant_id)
    plans: List[SessionRepairPlan] = []
    for s in db.scalars(stmt.order_by(ClassSession.starts_at.asc())).all():
        rows = db.scalars(select(Enrollment).where(
            Enrollment.session_id == s.id,
            Enrollment.status.in_(ACTIVE_SEAT_STATUSES))
            .order_by(Enrollment.created_at.asc(), Enrollment.id.asc())).all()
        max_seq = db.scalar(select(func.coalesce(func.max(Enrollment.waitlist_seq), 0)).where(
            Enrollment.session_id == s.id, Enrollment.status == 'WAITLIST')) or 0
        plan = SessionRepairPlan(
            session_id=s.id, tenant_id=s.tenant_id, course_name=s.course_name,
            capacity=int(s.capacity or 0), booked_before=int(s.booked or 0),
            active_before=len(rows), waitlist_max_seq=int(max_seq))
        plan.keep_ids = [e.id for e in rows[:plan.capacity]]
        for e in rows[plan.capacity:]:
            plan.demote_ids.append(e.id)
            plan.demote_members.append(e.member_id)
        if plan.needs_fix:
            plans.append(plan)
    return plans


def _repair_operator(db: Session, tenant_id: str) -> Dict:
    """审计操作人：优先取租户管理员，退化为系统账号。"""
    from ..models import User
    user = db.scalar(select(User).where(
        User.tenant_id == tenant_id, User.role == 'TENANT_ADMIN')) if tenant_id else None
    if user is None:
        return {'id': 'system', 'name': 'system'}
    return {'id': user.id, 'name': user.real_name or user.username}


def apply_repair(db: Session, plans: List[SessionRepairPlan], dry_run: bool = False) -> Dict:
    """执行修复。``dry_run=True`` 时只汇总、不落任何写操作。"""
    summary = {'sessions': 0, 'demoted': 0, 'recounted': 0, 'dry_run': bool(dry_run)}
    if dry_run:
        for plan in plans:
            summary['sessions'] += 1
            summary['demoted'] += len(plan.demote_ids)
            if plan.booked_before != plan.active_before:
                summary['recounted'] += 1
        return summary

    for plan in plans:
        operator = _repair_operator(db, plan.tenant_id)
        seq = plan.waitlist_max_seq
        for enr_id, member_id in zip(plan.demote_ids, plan.demote_members):
            enr = db.get(Enrollment, enr_id)
            if enr is None:
                continue
            seq += 1
            enr.status = 'WAITLIST'
            enr.waitlist_seq = seq
            enr.offer_expires_at = None
            db.add(AuditLog(
                id=random_id(), tenant_id=plan.tenant_id,
                actor_id=operator['id'], actor_name=operator['name'],
                action=DATA_REPAIR_ACTION, resource='enrollment', resource_id=enr_id,
                detail=('超员修复：课节 %s 容量 %d，该预约降级为候补（序号 %d）'
                        % (plan.session_id, plan.capacity, seq)),
                ip='',
            ))
            summary['demoted'] += 1
        db.flush()
        _recount_one(db, plan)
        summary['sessions'] += 1
        if plan.booked_before != plan.active_before:
            summary['recounted'] += 1
    return summary


def _recount_one(db: Session, plan: SessionRepairPlan) -> int:
    """单语句重算 booked 并同步 FULL/OPEN（与 services/schedule.py 同口径）。"""
    active = (select(func.count(Enrollment.id))
              .where(Enrollment.session_id == plan.session_id,
                     Enrollment.status.in_(ACTIVE_SEAT_STATUSES))
              .scalar_subquery())
    db.execute(
        ClassSession.__table__.update()
        .where(ClassSession.id == plan.session_id)
        .values(booked=active)
    )
    db.flush()
    session = db.get(ClassSession, plan.session_id)
    if session is None:
        return 0
    # 上面的 Core UPDATE 绕过了 ORM，必须让 identity map 失效后再判定状态
    db.refresh(session)
    if session.status not in ('CANCELLED', 'FINISHED', 'CLOSED'):
        session.status = 'FULL' if int(session.booked) >= int(session.capacity) else 'OPEN'
        db.flush()
    return int(session.booked or 0)


def format_report(plans: List[SessionRepairPlan], summary: Optional[Dict] = None) -> str:
    """把修复计划渲染成可粘贴给客户确认的文本报表。"""
    lines = ['课节名额修复报表', '=' * 72,
             '%-38s %4s %5s %6s %5s %5s' % ('课节', '容量', 'booked', '实际占位', '超员', '降级')]
    for plan in plans:
        lines.append('%-38s %4d %5d %6d %5d %5d' % (
            (plan.course_name or '')[:20] + '/' + plan.session_id[:12],
            plan.capacity, plan.booked_before, plan.active_before,
            plan.over, len(plan.demote_ids)))
    if summary is not None:
        lines.append('-' * 72)
        lines.append('合计：课节 %d 个，降级 %d 条，口径重算 %d 个%s' % (
            summary['sessions'], summary['demoted'], summary['recounted'],
            '（dry-run，未写库）' if summary.get('dry_run') else '（已写库）'))
    return '\n'.join(lines)


def tenant_ids(db: Session) -> List[str]:
    return list(db.scalars(select(Tenant.id)).all())
