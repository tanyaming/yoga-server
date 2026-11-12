"""超员修复回归（T-04 配套）：计划生成 / dry-run 不写库 / apply 降级并写审计。

注意：库级 CHECK 只能守住 ``booked`` 这个计数器，守不住「enrollments 里有几条 BOOKED」，
所以这里直接造 3 条 BOOKED + booked=1（合法）来模拟历史超卖脏数据。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update

from app.database import SessionLocal
from app.models import AuditLog, ClassSession, Enrollment
from app.services import schedule_repair as repair
from app.utils.timeutil import now_naive


def _dirty_session(world, *, booked_rows: int = 3, capacity: int = 1) -> str:
    factory = world['factory']
    session_id = factory.session(world['tenant_a'], capacity=capacity, hours_ahead=200)
    starts = now_naive() + timedelta(hours=200)
    with SessionLocal() as db:
        for i in range(booked_rows):
            db.add(Enrollment(
                id='enr-%s-%d' % (session_id[:8], i), tenant_id=world['tenant_a'],
                session_id=session_id, member_id=factory.member(world['tenant_a']),
                store_id=world['store_a'], status='BOOKED', source='MEMBER',
                waitlist_seq=0, created_at=starts + timedelta(minutes=i),
                updated_at=starts))
        # 计数器写成合法值（<= capacity），模拟「booked 与实际占位脱节」
        db.execute(update(ClassSession).where(ClassSession.id == session_id)
                   .values(booked=capacity).execution_options(synchronize_session=False))
        db.commit()
    return session_id


def test_build_plan_detects_overbooked(world):
    session_id = _dirty_session(world, booked_rows=3, capacity=1)
    with SessionLocal() as db:
        plans = [p for p in repair.build_repair_plan(db, world['tenant_a'])
                 if p.session_id == session_id]
    assert len(plans) == 1
    plan = plans[0]
    assert plan.capacity == 1
    assert plan.active_before == 3
    assert plan.over == 2
    assert len(plan.keep_ids) == 1
    assert len(plan.demote_ids) == 2
    # 按 created_at ASC 保留最早那条
    assert plan.keep_ids == ['enr-%s-0' % session_id[:8]]


def test_dry_run_does_not_write(world):
    session_id = _dirty_session(world, booked_rows=3, capacity=1)
    with SessionLocal() as db:
        plans = [p for p in repair.build_repair_plan(db, world['tenant_a'])
                 if p.session_id == session_id]
        summary = repair.apply_repair(db, plans, dry_run=True)
        db.commit()
    assert summary['dry_run'] is True
    assert summary['demoted'] == 2
    with SessionLocal() as db:
        active = db.scalar(select(func.count(Enrollment.id)).where(
            Enrollment.session_id == session_id, Enrollment.status == 'BOOKED')) or 0
        audits = db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == 'DATA_REPAIR',
            AuditLog.resource_id.in_([e for e in
                                      ['enr-%s-1' % session_id[:8], 'enr-%s-2' % session_id[:8]]]))) or 0
    assert active == 3, 'dry-run 不得改动数据'
    assert audits == 0, 'dry-run 不得写审计'


def test_apply_demotes_and_recounts(world):
    session_id = _dirty_session(world, booked_rows=3, capacity=1)
    with SessionLocal() as db:
        plans = [p for p in repair.build_repair_plan(db, world['tenant_a'])
                 if p.session_id == session_id]
        summary = repair.apply_repair(db, plans, dry_run=False)
        db.commit()
    assert summary['demoted'] == 2

    with SessionLocal() as db:
        db.expire_all()
        rows = db.scalars(select(Enrollment).where(Enrollment.session_id == session_id)
                          .order_by(Enrollment.waitlist_seq.asc())).all()
        statuses = {(r.id[-1], r.status) for r in rows}
        session = db.get(ClassSession, session_id)
        audits = db.scalars(select(AuditLog).where(
            AuditLog.action == 'DATA_REPAIR',
            AuditLog.resource == 'enrollment')).all()

    assert ('0', 'BOOKED') in statuses
    assert ('1', 'WAITLIST') in statuses and ('2', 'WAITLIST') in statuses
    assert session.booked == 1
    assert session.status == 'FULL'
    assert len(audits) >= 2, '每条降级都要留审计'
    assert all('超员修复' in a.detail for a in audits)


def test_format_report_renders(world):
    """报表必须带上明细：课节 ID、容量、降级条数、dry-run 标记缺一不可。"""
    session_id = _dirty_session(world, booked_rows=2, capacity=1)
    with SessionLocal() as db:
        plans = [p for p in repair.build_repair_plan(db, world['tenant_a'])
                 if p.session_id == session_id]
        summary = repair.apply_repair(db, plans, dry_run=True)
        text = repair.format_report(plans, summary)

    assert '课节名额修复报表' in text
    assert 'dry-run' in text
    assert session_id[:12] in text, '报表丢了课节明细行（只渲染了表头/合计）'
    assert '合计：课节 1 个，降级 1 条' in text, '报表的降级条数不对或未渲染合计行'
    # 明细行必须同时出现容量与实际占位两个数字，否则客户无法核对
    assert '容量' in text and '实际占位' in text


def test_clean_db_reports_nothing(world):
    """干净库不应报出任何待修课节（避免上线时误报刷屏）。"""
    with SessionLocal() as db:
        plans = repair.build_repair_plan(db, world['tenant_b'])
    assert plans == []
