"""课节名额守卫：超员数据修复 + booked 重算 + 索引 + CHECK 约束

Revision ID: a1b7c3d5e901
Revises: 7c1e9a2b3d4f
Create Date: 2026-11-12

为什么必须按这个顺序做（换顺序会失败或误伤）：

1. 先用「按 created_at ASC 保留前 capacity 条」把超员 BOOKED/OFFERED 降级为 WAITLIST；
2. 再用 COUNT(BOOKED, OFFERED) 重算 booked —— 先重算会算出 > capacity 的值，CHECK 加不上；
3. 再建 (session_id, status) 索引 —— 重算与点名名单都依赖它；
4. 最后加 CHECK(booked >= 0 AND booked <= capacity)。SQLite 不支持 ADD CONSTRAINT，
   必须用 batch_alter_table 重建表。

数据修复不删任何记录，只把多出来的预约退回候补队列，并写 AuditLog(action='DATA_REPAIR')。
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = 'a1b7c3d5e901'
down_revision = '7c1e9a2b3d4f'
branch_labels = None
depends_on = None

_ACTIVE = ('BOOKED', 'OFFERED')


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _write_repair_audit(bind, tenant_id: str, enrollment_id: str, session_id: str,
                        member_id: str, capacity: int, seq: int) -> None:
    """漂移修复留痕：迁移内直接写文本 SQL，避免依赖应用层的 ORM 版本。"""
    detail = ('超员修复：课节 %s 容量 %d，会员 %s 的预约降级为候补（序号 %d）'
              % (session_id, capacity, member_id, seq))
    bind.execute(
        sa.text(
            'INSERT INTO audit_logs (id, actor_id, actor_name, action, resource, resource_id, '
            'detail, ip, tenant_id, created_at, updated_at) VALUES '
            '(:id, :actor_id, :actor_name, :action, :resource, :resource_id, '
            ':detail, :ip, :tenant_id, :created_at, :updated_at)'),
        {'id': str(uuid.uuid4()), 'actor_id': 'system', 'actor_name': 'system',
         'action': 'DATA_REPAIR', 'resource': 'enrollment', 'resource_id': enrollment_id,
         'detail': detail, 'ip': '', 'tenant_id': tenant_id,
         'created_at': _now(), 'updated_at': _now()},
    )


def _demote_overbooked(bind) -> int:
    """步骤 1：超员 BOOKED/OFFERED 按 created_at ASC 保留前 capacity 条，其余降 WAITLIST。"""
    demoted = 0
    sessions = bind.execute(sa.text(
        'SELECT id, tenant_id, course_name, capacity FROM class_sessions'
    )).mappings().all()
    for s in sessions:
        capacity = max(0, int(s['capacity'] or 0))
        rows = bind.execute(
            sa.text(
                "SELECT id, member_id FROM enrollments "
                "WHERE session_id = :sid AND status IN ('BOOKED', 'OFFERED') "
                "ORDER BY created_at ASC, id ASC"),
            {'sid': s['id']},
        ).mappings().all()
        if len(rows) <= capacity:
            continue
        max_seq = bind.execute(
            sa.text(
                "SELECT COALESCE(MAX(waitlist_seq), 0) FROM enrollments "
                "WHERE session_id = :sid AND status = 'WAITLIST'"),
            {'sid': s['id']},
        ).scalar() or 0
        for offset, row in enumerate(rows[capacity:], start=1):
            seq = int(max_seq) + offset
            bind.execute(
                sa.text(
                    "UPDATE enrollments SET status = 'WAITLIST', waitlist_seq = :seq, "
                    "offer_expires_at = NULL WHERE id = :id"),
                {'seq': seq, 'id': row['id']},
            )
            _write_repair_audit(bind, s['tenant_id'], row['id'], s['id'],
                                row['member_id'], capacity, seq)
            demoted += 1
    return demoted


def _recount_booked(bind) -> None:
    """步骤 2：booked = COUNT(BOOKED, OFFERED)，并同步 FULL/OPEN。"""
    bind.execute(sa.text(
        "UPDATE class_sessions SET booked = ("
        "  SELECT COUNT(*) FROM enrollments e"
        "  WHERE e.session_id = class_sessions.id AND e.status IN ('BOOKED', 'OFFERED')"
        ")"))
    bind.execute(sa.text(
        "UPDATE class_sessions SET status = CASE"
        "  WHEN status IN ('CANCELLED', 'FINISHED', 'CLOSED') THEN status"
        "  WHEN booked >= capacity THEN 'FULL' ELSE 'OPEN' END"))


def upgrade() -> None:
    bind = op.get_bind()

    # ① 超员降级（必须在加 CHECK 之前，否则 CHECK 建不上）
    _demote_overbooked(bind)
    # ② 口径重算
    _recount_booked(bind)
    # ③ 索引：名额重算与点名名单都按 (session_id, status) 过滤
    op.create_index('ix_enroll_session_status', 'enrollments',
                    ['session_id', 'status'], unique=False)
    # ④ CHECK：库级兜底，任何绕过服务的写入都无法造出超卖
    with op.batch_alter_table('class_sessions', recreate='always') as batch:
        batch.create_check_constraint(
            'ck_session_booked_range', 'booked >= 0 AND booked <= capacity')


def downgrade() -> None:
    # 只回退结构，不回退数据修复（降级不得重新造出超员数据）
    with op.batch_alter_table('class_sessions', recreate='always') as batch:
        batch.drop_constraint('ck_session_booked_range', type_='check')
    op.drop_index('ix_enroll_session_status', table_name='enrollments')
