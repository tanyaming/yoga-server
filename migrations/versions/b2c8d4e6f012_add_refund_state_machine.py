"""账务状态机：收款单累计退款额 + 退款单维度字段 + 幂等键按租户收口

Revision ID: b2c8d4e6f012
Revises: a1b7c3d5e901
Create Date: 2026-11-12

原则：只加列、不删列；所有历史数据以幂等回填方式修正，可重复执行不产生副作用。

步骤：
1. 先校验现有幂等键在「(tenant_id, idempotency_key)」下是否已重复；
   有重复直接 raise 并打印冲突键（R7），不允许带着歧义上线；
2. payment_records 加 refunded_cents（默认 0）；唯一键从 (idempotency_key)
   改为 (tenant_id, idempotency_key)；
3. ledger_entries 唯一键同样补 tenant_id；
4. refund_records 加 refund_sessions / status / store_id / idempotency_key
   + 唯一键 (tenant_id, idempotency_key)；
5. 历史回填：refunded_cents = 退款单汇总；status 由 0/部分/全部 推导为
   PAID / PARTIAL_REFUNDED / REFUNDED；refund_records.store_id 从收款单继承。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b2c8d4e6f012'
down_revision = 'a1b7c3d5e901'
branch_labels = None
depends_on = None


def _assert_no_duplicate_keys(bind, table: str) -> None:
    """迁移前置校验：唯一键收窄范围前必须先确认目标范围内没有重复。"""
    rows = bind.execute(sa.text(
        'SELECT tenant_id, idempotency_key, COUNT(*) AS c FROM %s '
        "WHERE idempotency_key IS NOT NULL AND idempotency_key <> '' "
        'GROUP BY tenant_id, idempotency_key HAVING COUNT(*) > 1' % table
    )).mappings().all()
    if rows:
        detail = '; '.join('tenant=%s key=%s x%s' % (r['tenant_id'], r['idempotency_key'], r['c'])
                           for r in rows)
        raise RuntimeError(
            '迁移中止：%s 存在重复幂等键，请先人工归并后再升级。冲突键：%s' % (table, detail))


def _swap_unique_key(bind, table: str, old_name: str, new_name: str) -> None:
    """把唯一约束改为 (tenant_id, idempotency_key)。SQLite 走 batch 重建表。"""
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(old_name, type_='unique')
        batch.create_unique_constraint(new_name, ['tenant_id', 'idempotency_key'])


def upgrade() -> None:
    bind = op.get_bind()

    # 1. 前置校验（有冲突直接失败，绝不带歧义上线）
    _assert_no_duplicate_keys(bind, 'payment_records')
    _assert_no_duplicate_keys(bind, 'ledger_entries')

    # 2. 收款单：累计退款额 + 幂等键补 tenant_id
    with op.batch_alter_table('payment_records') as batch:
        batch.add_column(sa.Column('refunded_cents', sa.Integer(),
                                   nullable=False, server_default='0'))
        batch.drop_constraint('uq_payment_idem', type_='unique')
        batch.create_unique_constraint('uq_payment_idem_tenant',
                                       ['tenant_id', 'idempotency_key'])

    # 3. 流水：幂等键补 tenant_id
    _swap_unique_key(bind, 'ledger_entries', 'uq_ledger_idem', 'uq_ledger_idem_tenant')

    # 4. 退款单：补齐课时/状态/门店/幂等键
    with op.batch_alter_table('refund_records') as batch:
        batch.add_column(sa.Column('refund_sessions', sa.Integer(),
                                   nullable=False, server_default='0'))
        batch.add_column(sa.Column('status', sa.String(length=20),
                                   nullable=False, server_default='DONE'))
        batch.add_column(sa.Column('store_id', sa.String(length=36),
                                   nullable=False, server_default=''))
        batch.add_column(sa.Column('idempotency_key', sa.String(length=64), nullable=True))
        batch.create_unique_constraint('uq_refund_idem_tenant',
                                       ['tenant_id', 'idempotency_key'])
    # 退款单按收款单回溯是高频查询（账实核对、后台详情）
    op.create_index('ix_refund_payment', 'refund_records', ['payment_id'], unique=False)

    # 5. 历史回填（幂等：重复执行结果一致）
    bind.execute(sa.text(
        'UPDATE refund_records SET store_id = COALESCE(('
        '  SELECT p.store_id FROM payment_records p WHERE p.id = refund_records.payment_id'
        "), '') WHERE store_id = ''"))
    bind.execute(sa.text(
        'UPDATE payment_records SET refunded_cents = COALESCE(('
        '  SELECT SUM(r.refund_cents) FROM refund_records r'
        '  WHERE r.payment_id = payment_records.id'
        '), 0)'))
    # 已退金额不得超过收款金额（历史脏数据截断，避免净额为负）
    bind.execute(sa.text(
        'UPDATE payment_records SET refunded_cents = amount_cents '
        'WHERE refunded_cents > amount_cents'))
    bind.execute(sa.text(
        "UPDATE payment_records SET status = CASE"
        "  WHEN refunded_cents <= 0 THEN 'PAID'"
        "  WHEN refunded_cents >= amount_cents THEN 'REFUNDED'"
        "  ELSE 'PARTIAL_REFUNDED' END"
        " WHERE status = 'PAID'"))


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index('ix_refund_payment', table_name='refund_records')
    with op.batch_alter_table('refund_records') as batch:
        batch.drop_constraint('uq_refund_idem_tenant', type_='unique')
        batch.drop_column('idempotency_key')
        batch.drop_column('store_id')
        batch.drop_column('status')
        batch.drop_column('refund_sessions')

    _swap_unique_key(bind, 'ledger_entries', 'uq_ledger_idem_tenant', 'uq_ledger_idem')

    # 降级前把状态还原为旧口径（旧代码只认 PAID）
    bind.execute(sa.text(
        "UPDATE payment_records SET status = 'PAID' "
        "WHERE status IN ('PARTIAL_REFUNDED', 'REFUNDED')"))
    with op.batch_alter_table('payment_records') as batch:
        batch.drop_constraint('uq_payment_idem_tenant', type_='unique')
        batch.create_unique_constraint('uq_payment_idem', ['idempotency_key'])
        batch.drop_column('refunded_cents')
