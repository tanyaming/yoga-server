"""补齐 PRD 范围缺口：卡种/跨店规则、候补锁定、通知通道、调课审批、教练评价

Revision ID: 7c1e9a2b3d4f
Revises: 6ecded13fd8f
Create Date: 2026-11-12

新增列全部给空值默认，存量数据保持「不限制」语义，升级不改写历史业务数据。
"""
from alembic import op
import sqlalchemy as sa


revision = '7c1e9a2b3d4f'
down_revision = '6ecded13fd8f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # FR-07 / FR-16：商品适用门店与可约课型
    op.add_column('products', sa.Column('applicable_store_ids', sa.String(length=240),
                                        nullable=False, server_default=''))
    op.add_column('products', sa.Column('allowed_ctypes', sa.String(length=80),
                                        nullable=False, server_default=''))
    # FR-16：权益上的规则快照（商品后续变更不影响已售权益）
    op.add_column('entitlements', sa.Column('cross_store', sa.Boolean(),
                                            nullable=False, server_default=sa.text('0')))
    op.add_column('entitlements', sa.Column('applicable_store_ids', sa.String(length=240),
                                            nullable=False, server_default=''))
    op.add_column('entitlements', sa.Column('allowed_ctypes', sa.String(length=80),
                                            nullable=False, server_default=''))
    # FR-13：课程模板可约卡种
    op.add_column('course_templates', sa.Column('allowed_ptypes', sa.String(length=80),
                                                nullable=False, server_default=''))
    # FR-14：候补递补确认锁定的过期时间
    op.add_column('enrollments', sa.Column('offer_expires_at', sa.DateTime(), nullable=True))
    # FR-21：统一通知通道留痕
    op.add_column('in_app_messages', sa.Column('scene', sa.String(length=40),
                                               nullable=False, server_default=''))
    op.add_column('in_app_messages', sa.Column('channel_status', sa.String(length=30),
                                               nullable=False, server_default='IN_APP_ONLY'))
    op.add_column('in_app_messages', sa.Column('channel_detail', sa.String(length=240),
                                               nullable=False, server_default=''))

    # FR-12：调课 / 停课申请单
    op.create_table(
        'schedule_change_requests',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('requester_id', sa.String(length=36), nullable=False),
        sa.Column('requester_name', sa.String(length=60), nullable=False),
        sa.Column('change_type', sa.String(length=20), nullable=False),
        sa.Column('new_starts_at', sa.DateTime(), nullable=True),
        sa.Column('new_coach_id', sa.String(length=36), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('approver_id', sa.String(length=36), nullable=False),
        sa.Column('review_note', sa.String(length=240), nullable=False),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_change_session_status', 'schedule_change_requests',
                    ['session_id', 'status'], unique=False)
    op.create_index(op.f('ix_schedule_change_requests_tenant_id'),
                    'schedule_change_requests', ['tenant_id'], unique=False)

    # FR-20：教练评价（先审后显）
    op.create_table(
        'coach_reviews',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('member_id', sa.String(length=36), nullable=False),
        sa.Column('coach_id', sa.String(length=36), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reject_reason', sa.String(length=240), nullable=False),
        sa.Column('moderated_by', sa.String(length=36), nullable=False),
        sa.Column('moderated_at', sa.DateTime(), nullable=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'member_id', name='uq_review_session_member'),
    )
    op.create_index('ix_review_coach_status', 'coach_reviews', ['coach_id', 'status'], unique=False)
    op.create_index(op.f('ix_coach_reviews_tenant_id'), 'coach_reviews', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_coach_reviews_member_id'), 'coach_reviews', ['member_id'], unique=False)
    op.create_index(op.f('ix_coach_reviews_session_id'), 'coach_reviews', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_coach_reviews_session_id'), table_name='coach_reviews')
    op.drop_index(op.f('ix_coach_reviews_member_id'), table_name='coach_reviews')
    op.drop_index(op.f('ix_coach_reviews_tenant_id'), table_name='coach_reviews')
    op.drop_index('ix_review_coach_status', table_name='coach_reviews')
    op.drop_table('coach_reviews')
    op.drop_index(op.f('ix_schedule_change_requests_tenant_id'), table_name='schedule_change_requests')
    op.drop_index('ix_change_session_status', table_name='schedule_change_requests')
    op.drop_table('schedule_change_requests')
    op.drop_column('in_app_messages', 'channel_detail')
    op.drop_column('in_app_messages', 'channel_status')
    op.drop_column('in_app_messages', 'scene')
    op.drop_column('enrollments', 'offer_expires_at')
    op.drop_column('course_templates', 'allowed_ptypes')
    op.drop_column('entitlements', 'allowed_ctypes')
    op.drop_column('entitlements', 'applicable_store_ids')
    op.drop_column('entitlements', 'cross_store')
    op.drop_column('products', 'allowed_ctypes')
    op.drop_column('products', 'applicable_store_ids')
