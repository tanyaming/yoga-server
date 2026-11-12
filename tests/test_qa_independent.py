"""QA 独立验收用例（严过关自建）。

**独立性声明**：本文件不复用 `tests/test_concurrency_booking.py` 与
`tests/test_payment_refund_dashboard.py` 的任何断言与辅助函数，全部自行造数据、
自行断言；不改动 `app/` 下任何源码，也不改动开发者写的用例。

覆盖：
- A 并发约课（10 轮 × 容量 1 × 5 会员、容量 3 × 7 会员、原子占位源码审查、identity map 刷新）
- B 账务（全额退回冲、部分退、幂等、超退、VOID、负余额误判）
- C 回归（迁移升降级、脏数据升级修复 + CHECK、HTTP 主链路、取消后重约、点名回算、权限）
- D 质量（源码 500 路径与方言扫描在报告中人工给出，这里只放可自动化的部分）
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.database import SessionLocal
from app.models import (ClassSession, DailyLedger, Enrollment, Entitlement,
                        LedgerEntry, PaymentRecord, ReconciliationDiff,
                        RefundRecord)
from app.services import schedule as sch
from app.utils.timeutil import business_date, now_naive

REPO_ROOT = Path(__file__).resolve().parent.parent
SEAT_STATUSES = ('BOOKED', 'OFFERED')


# =====================================================================
# 通用辅助（全部自建，不依赖开发者用例）
# =====================================================================
class Ctx:
    """一次验收里反复用到的租户/门店/账号常量与工厂快捷方式。"""

    def __init__(self, world):
        self.world = world
        self.factory = world['factory']
        self.tenant = world['tenant_a']
        self.store = world['store_a']
        self.other_store = world['store_b']

    def member(self, n: int = 1):
        return [self.factory.member(self.tenant) for _ in range(n)]

    def member_with_card(self, sessions: int = 10, price: int = 199900):
        """会员 + 一张 ACTIVE 课时卡（直接建权益，走约课不涉及收款）。"""
        mid = self.factory.member(self.tenant, '验收会员' + uuid.uuid4().hex[:4])
        self.factory.entitlement(self.tenant, mid, total_sessions=sessions,
                                 price_cents=price)
        return mid

    def session(self, capacity: int = 1, hours_ahead: int = 72) -> str:
        return self.factory.session(self.tenant, store_id=self.store,
                                    capacity=capacity, hours_ahead=hours_ahead)


@pytest.fixture
def ctx(world):
    return Ctx(world)


def _snapshot(session_id: str) -> dict:
    """强制重读：booked / 实际占位人数 / capacity / status。"""
    with SessionLocal() as db:
        db.expire_all()
        s = db.get(ClassSession, session_id)
        assert s is not None, '课节不存在：%s' % session_id
        active = int(db.scalar(select(func.count(Enrollment.id)).where(
            Enrollment.session_id == session_id,
            Enrollment.status.in_(SEAT_STATUSES))) or 0)
        return {'booked': int(s.booked), 'active': active,
                'capacity': int(s.capacity), 'status': s.status}


def _book_once(member_id: str, session_id: str, tenant: str = 't-a') -> dict:
    """独立会话约课并提交（串行场景用）。"""
    with SessionLocal() as db:
        out = sch.book(db, tenant, member_id, session_id, 'MEMBER')
        db.commit()
        return out


def _parallel_book(member_ids, session_id: str, tenant: str = 't-a'):
    """Barrier 同步起跑 + 每人独立 Session 的并发约课；返回 (结果, 异常)。"""
    barrier = threading.Barrier(len(member_ids), timeout=30)
    results, errors = [], []
    guard = threading.Lock()

    def worker(mid: str) -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            out = sch.book(db, tenant, mid, session_id, 'MEMBER')
            db.commit()
            with guard:
                results.append({'member_id': mid, **out})
        except BaseException as exc:  # noqa: BLE001 - 汇总后在断言里统一暴露
            with guard:
                errors.append({'member_id': mid, 'error': '%s: %s'
                               % (type(exc).__name__, exc)})
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(m,)) for m in member_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    alive = [t for t in threads if t.is_alive()]
    return results, errors, alive


# =====================================================================
# A. 并发约课
# =====================================================================
ROUNDS = 10


def test_qa_A1_concurrent_capacity_one_10_rounds(ctx):
    """10 轮：容量 1、5 个不同会员并发、Barrier 起跑、各自独立 Session。

    每轮硬断言：BOOKED==1、WAITLIST==4、booked==1、
    booked==COUNT(BOOKED,OFFERED)、课节 status==FULL。
    任何一轮不满足即失败，并打印该轮完整数据。
    """
    report_lines = []
    failures = []

    for rnd in range(1, ROUNDS + 1):
        session_id = ctx.session(capacity=1, hours_ahead=72 + rnd)
        members = [ctx.member_with_card(10) for _ in range(5)]

        results, errors, alive = _parallel_book(members, session_id)
        snap = _snapshot(session_id)
        booked_cnt = len([r for r in results if r['status'] == 'BOOKED'])
        wait_cnt = len([r for r in results if r['status'] == 'WAITLIST'])

        row = {
            'round': rnd, 'session': session_id[:10],
            'BOOKED': booked_cnt, 'WAITLIST': wait_cnt,
            'booked': snap['booked'], 'active': snap['active'],
            'capacity': snap['capacity'], 'status': snap['status'],
            'errors': len(errors), 'alive': len(alive),
        }
        report_lines.append(row)

        problems = []
        if alive:
            problems.append('线程未在超时内结束：%d' % len(alive))
        if errors:
            problems.append('并发异常：%s' % errors)
        if booked_cnt != 1:
            problems.append('BOOKED 数 %d != 1（超卖）' % booked_cnt)
        if wait_cnt != 4:
            problems.append('WAITLIST 数 %d != 4' % wait_cnt)
        if snap['booked'] != 1:
            problems.append('booked %d != 1' % snap['booked'])
        if snap['booked'] != snap['active']:
            problems.append('计数漂移：booked=%d 但实际占位=%d'
                            % (snap['booked'], snap['active']))
        if snap['status'] != 'FULL':
            problems.append('课节状态 %s != FULL' % snap['status'])
        if problems:
            failures.append({'round': rnd, 'session_id': session_id,
                             'problems': problems, 'raw_results': results,
                             'errors': errors})

    print('\n[A1] 10 轮并发明细（capacity=1，5 会员）：')
    for row in report_lines:
        print('  R%(round)02d session=%(session)s BOOKED=%(BOOKED)d WAITLIST=%(WAITLIST)d '
              'booked=%(booked)d active=%(active)d cap=%(capacity)d '
              'status=%(status)s errors=%(errors)d' % row)
    if failures:
        for f in failures:
            print('\n[A1] 失败轮次 %s 完整数据：%s' % (f['round'], f['session_id']))
            print('  problems: %s' % f['problems'])
            print('  results : %s' % f['raw_results'])
            print('  errors  : %s' % f['errors'])
    assert not failures, '并发约课存在 %d/%d 轮不达标，见上方逐轮数据' % (len(failures), ROUNDS)


def test_qa_A2_concurrent_capacity_three_seven_members(ctx):
    """容量 3、7 人并发 → 恰好 3 BOOKED + 4 WAITLIST，且候补序号不重复。"""
    session_id = ctx.session(capacity=3, hours_ahead=200)
    members = [ctx.member_with_card(10) for _ in range(7)]

    results, errors, alive = _parallel_book(members, session_id)
    snap = _snapshot(session_id)
    booked = [r for r in results if r['status'] == 'BOOKED']
    waitlist = [r for r in results if r['status'] == 'WAITLIST']
    seqs = sorted(r['waitlist_seq'] for r in waitlist)

    print('\n[A2] capacity=3 / 7 并发：BOOKED=%d WAITLIST=%d booked=%d active=%d '
          'status=%s seqs=%s errors=%s'
          % (len(booked), len(waitlist), snap['booked'], snap['active'],
             snap['status'], seqs, errors))

    assert not alive, '并发线程未结束'
    assert not errors, '并发异常：%s' % errors
    assert len(booked) == 3, '超卖：BOOKED=%d（期望 3）' % len(booked)
    assert len(waitlist) == 4, 'WAITLIST=%d（期望 4）' % len(waitlist)
    assert snap['booked'] == 3 and snap['active'] == 3, 'booked/active 不等于 3：%s' % snap
    assert snap['status'] == 'FULL'
    assert seqs == [1, 2, 3, 4], '候补序号并列或跳号：%s' % seqs


def test_qa_A3_source_review_atomic_placeholder():
    """代码审查：占位必须是单语句原子条件 UPDATE，且全项目没有 with_for_update()。

    这类约束只有读源码才能验证（跑用例验证不到"实现方式"），因此单独成用例。
    """
    src = (REPO_ROOT / 'app' / 'services' / 'schedule.py').read_text(encoding='utf-8')

    # ① 全项目禁止 with_for_update（SQLite 静默忽略 → 假锁）
    #    只匹配真实调用 ".with_for_update("，避免把"禁止 with_for_update()"这类
    #    说明性注释误判成违规（QA 自测时踩过一次，故这里注明）。
    call_re = re.compile(r'\.\s*with_for_update\s*\(')
    offenders = []
    for path in (REPO_ROOT / 'app').rglob('*.py'):
        if call_re.search(path.read_text(encoding='utf-8')):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], '发现 with_for_update() 调用：%s' % offenders

    # ② 取出 _try_take_seat 函数体，逐条确认"条件 UPDATE 单语句占位"
    body = re.search(r'def _try_take_seat.*?(?=\ndef )', src, re.S)
    assert body, '未找到 _try_take_seat 实现（占位逻辑被重构？需人工复核）'
    body = body.group(0)
    assert 'update(ClassSession)' in body, '_try_take_seat 不是 UPDATE 语句'
    assert 'ClassSession.booked < ClassSession.capacity' in body, \
        'UPDATE 缺少 booked < capacity 原子条件（会退化成读-改-写）'
    assert 'booked=ClassSession.booked + 1' in body, \
        'UPDATE 未在库内自增（说明把值从 Python 读出来再写回，存在丢失更新）'
    assert 'rowcount == 1' in body, '占位未用 rowcount 判定命中'

    # ③ 占位后必须刷新 identity map，否则同请求回显旧 booked
    assert 'db.expire(s)' in src, 'book() 占位后未刷新课节对象（详情页会回显旧值）'
    assert 'synchronize_session=False' in body, 'Core UPDATE 未关闭 ORM 同步，语义不可控'


def test_qa_A4_identity_map_refreshed_after_book(ctx):
    """占位后同一 Session 内读课节，booked 必须是新值（不是缓存里的 0）。"""
    session_id = ctx.session(capacity=2, hours_ahead=210)
    mid = ctx.member_with_card(10)

    with SessionLocal() as db:
        cached = db.get(ClassSession, session_id)
        assert int(cached.booked) == 0, '前置条件不成立：初始 booked 非 0'
        out = sch.book(db, ctx.tenant, mid, session_id, 'MEMBER')
        db.commit()
        assert out['status'] == 'BOOKED'
        # 访问同一对象属性：若未 expire 会读到缓存里的 0
        assert int(cached.booked) == 1, \
            '同一 Session 内回显旧值 booked=%d（identity map 未刷新）' % cached.booked


# =====================================================================
# B. 账务
# =====================================================================
def _api_pay(client, headers, *, store_id, member_id, product_id, amount_cents,
             key: str = '') -> dict:
    resp = client.post('/api/payments', headers=headers, json={
        'store_id': store_id, 'member_id': member_id, 'product_id': product_id,
        'amount_cents': amount_cents, 'method': 'CASH', 'idempotency_key': key})
    assert resp.status_code == 200, '收款失败 %s: %s' % (resp.status_code, resp.text)
    return resp.json()


def _api_product(client, headers, *, ptype: str, sessions: int, price: int) -> str:
    resp = client.post('/api/payments/products', headers=headers, json={
        'name': 'QA验收商品' + uuid.uuid4().hex[:4], 'ptype': ptype,
        'total_sessions': sessions, 'price_cents': price})
    assert resp.status_code == 200, '建商品失败：%s' % resp.text
    return resp.json()['id']


def _overview(client, headers) -> dict:
    resp = client.get('/api/dashboard/overview', headers=headers)
    assert resp.status_code == 200, '看板失败：%s' % resp.text
    return resp.json()


def test_qa_B1_full_refund_reverses_income(client, tokens, ctx):
    """收款 1999 元 → 全额退 → 看板本月收入必须减少 199900 分。"""
    mid = ctx.factory.member(ctx.tenant)
    product_id = _api_product(client, tokens['a'], ptype='PACKAGE',
                              sessions=10, price=199900)
    pay = _api_pay(client, tokens['a'], store_id=ctx.store, member_id=mid,
                   product_id=product_id, amount_cents=199900)
    assert pay['status'] == 'PAID' and pay['refunded_cents'] == 0

    before = _overview(client, tokens['a'])
    resp = client.post('/api/payments/refund', headers=tokens['a'],
                       json={'entitlement_id': pay['entitlement_id']})
    assert resp.status_code == 200, '全额退款失败：%s' % resp.text
    body = resp.json()

    print('\n[B1] 全额退：refund_cents=%s payment_status=%s income %s -> %s'
          % (body['refund_cents'], body['payment_status'],
             before['month_income_cents'], _overview(client, tokens['a'])['month_income_cents']))

    assert body['refund_cents'] == 199900, '退款额 %s != 199900' % body['refund_cents']
    assert body['payment_status'] == 'REFUNDED'
    assert body['refunded_cents'] == 199900
    assert body['refundable_cents'] == 0
    assert body['ledger_entry_id'], '退款未写流水/未回写 ledger_entry_id'

    after = _overview(client, tokens['a'])
    assert after['month_income_cents'] == before['month_income_cents'] - 199900, \
        '退款未回冲营收：before=%d after=%d' % (before['month_income_cents'],
                                                after['month_income_cents'])
    assert after['month_refund_cents'] - before['month_refund_cents'] == 199900
    assert after['month_gross_cents'] == before['month_gross_cents'], '退款不应改变毛收款'

    with SessionLocal() as db:
        stored = db.get(PaymentRecord, pay['id'])
        assert stored.status == 'REFUNDED', '单据状态 %s != REFUNDED' % stored.status
        assert stored.refunded_cents == stored.amount_cents, 'refunded_cents != amount'
        ref = db.scalar(select(RefundRecord).where(RefundRecord.payment_id == pay['id']))
        assert ref is not None and ref.refund_cents == 199900
        ledger = db.get(LedgerEntry, ref.ledger_entry_id)
        assert ledger is not None and ledger.kind == 'REFUND'
        assert ledger.delta_cents == -199900

    # 日终台账必须回冲退款
    recon = client.post('/api/dashboard/reconcile', headers=tokens['a'],
                        json={'store_id': ctx.store, 'date': business_date()})
    assert recon.status_code == 200, recon.text
    assert recon.json()['refund_cents'] > 0, '日终台账 refund_cents 未回冲：%s' % recon.json()
    with SessionLocal() as db:
        row = db.scalar(select(DailyLedger).where(
            DailyLedger.tenant_id == ctx.tenant, DailyLedger.store_id == ctx.store,
            DailyLedger.biz_date == business_date()))
        assert row is not None and int(row.refund_cents) > 0, '台账快照未落 refund_cents'


def test_qa_B2_partial_refund_two_of_ten(client, tokens, ctx):
    """10 节课退 2 节 → 退款额 == round(1999×2/10)，单据 PARTIAL_REFUNDED，权益口径正确。"""
    mid = ctx.factory.member(ctx.tenant)
    product_id = _api_product(client, tokens['a'], ptype='PACKAGE',
                              sessions=10, price=199900)
    pay = _api_pay(client, tokens['a'], store_id=ctx.store, member_id=mid,
                   product_id=product_id, amount_cents=199900)

    expected = round(199900 * 2 / 10)
    resp = client.post('/api/payments/refund', headers=tokens['a'],
                       json={'entitlement_id': pay['entitlement_id'], 'refund_sessions': 2})
    assert resp.status_code == 200, '部分退款失败：%s' % resp.text
    body = resp.json()

    print('\n[B2] 部分退 2/10：refund_cents=%s expected=%s status=%s used=%s refundable=%s'
          % (body['refund_cents'], expected, body['payment_status'],
             body.get('refund_sessions'), body['refundable_cents']))

    assert body['refund_cents'] == expected, '退款额 %s != %s' % (body['refund_cents'], expected)
    assert body['refund_sessions'] == 2
    assert body['payment_status'] == 'PARTIAL_REFUNDED'
    assert body['refundable_cents'] == 199900 - expected
    assert body['formula'], '按课时退款必须留可复核公式'

    with SessionLocal() as db:
        pay_row = db.get(PaymentRecord, pay['id'])
        assert pay_row.status == 'PARTIAL_REFUNDED'
        assert pay_row.refunded_cents == expected
        ent = db.get(Entitlement, pay['entitlement_id'])
        assert ent.status == 'ACTIVE', '部分退款不应终止权益'
        assert ent.used_sessions == 2, '退掉的 2 节应计回 used_sessions，实际 %d' % ent.used_sessions

    # 收款单列表回显的 refundable 必须一致
    listed = client.get('/api/payments', headers=tokens['a']).json()
    row = next(p for p in listed if p['id'] == pay['id'])
    assert row['status'] == 'PARTIAL_REFUNDED'
    assert row['refundable_cents'] == 199900 - expected


def test_qa_B3_refund_idempotent_no_double_deduction(client, tokens, ctx):
    """同一 idempotency_key 重复退：金额与流水条数都不变。"""
    mid = ctx.factory.member(ctx.tenant)
    product_id = _api_product(client, tokens['a'], ptype='PACKAGE',
                              sessions=10, price=199900)
    pay = _api_pay(client, tokens['a'], store_id=ctx.store, member_id=mid,
                   product_id=product_id, amount_cents=199900)
    key = 'qa-refund-' + uuid.uuid4().hex[:12]
    payload = {'entitlement_id': pay['entitlement_id'], 'refund_sessions': 2,
               'idempotency_key': key}

    income_before = _overview(client, tokens['a'])['month_income_cents']
    first = client.post('/api/payments/refund', headers=tokens['a'], json=payload)
    assert first.status_code == 200, first.text
    income_after_first = _overview(client, tokens['a'])['month_income_cents']

    second = client.post('/api/payments/refund', headers=tokens['a'], json=payload)
    assert second.status_code == 200, second.text
    third = client.post('/api/payments/refund', headers=tokens['a'], json=payload)
    assert third.status_code == 200, third.text

    print('\n[B3] 幂等退：first=%s second=%s third=%s idempotent=%s/%s income %s->%s'
          % (first.json()['refund_cents'], second.json()['refund_cents'],
             third.json()['refund_cents'], second.json()['idempotent'],
             third.json()['idempotent'], income_before, income_after_first))

    assert second.json()['id'] == first.json()['id'] == third.json()['id'], '重复生成退款单'
    assert second.json()['idempotent'] is True
    assert second.json()['refunded_cents'] == first.json()['refunded_cents'] == 39980

    income_after_third = _overview(client, tokens['a'])['month_income_cents']
    assert income_after_third == income_after_first, \
        '幂等重放二次扣减：%d -> %d' % (income_after_first, income_after_third)
    assert income_before - income_after_first == 39980

    with SessionLocal() as db:
        refs = db.scalars(select(RefundRecord).where(
            RefundRecord.tenant_id == ctx.tenant,
            RefundRecord.idempotency_key == key)).all()
        assert len(refs) == 1, '同一幂等键写了 %d 张退款单' % len(refs)
        ledgers = db.scalars(select(LedgerEntry).where(
            LedgerEntry.kind == 'REFUND',
            LedgerEntry.ref_id == refs[0].id)).all()
        assert len(ledgers) == 1, '同一退款单写了 %d 条 REFUND 流水' % len(ledgers)
        assert db.get(PaymentRecord, pay['id']).refunded_cents == 39980


def test_qa_B4_over_refund_is_4xx_and_no_side_effect(client, tokens, ctx):
    """超退（退 3 节后再退 9 节）必须 4xx，且不产生退款单/流水。"""
    mid = ctx.factory.member(ctx.tenant)
    product_id = _api_product(client, tokens['a'], ptype='PACKAGE',
                              sessions=10, price=199900)
    pay = _api_pay(client, tokens['a'], store_id=ctx.store, member_id=mid,
                   product_id=product_id, amount_cents=199900)

    first = client.post('/api/payments/refund', headers=tokens['a'],
                        json={'entitlement_id': pay['entitlement_id'], 'refund_sessions': 3})
    assert first.status_code == 200, first.text

    with SessionLocal() as db:
        refs_before = db.scalar(select(func.count(RefundRecord.id)).where(
            RefundRecord.payment_id == pay['id']))
        ledgers_before = db.scalar(select(func.count(LedgerEntry.id)).where(
            LedgerEntry.kind == 'REFUND',
            LedgerEntry.ref_id.in_(select(RefundRecord.id).where(
                RefundRecord.payment_id == pay['id']))))
        refunded_before = int(db.get(PaymentRecord, pay['id']).refunded_cents)

    resp = client.post('/api/payments/refund', headers=tokens['a'],
                       json={'entitlement_id': pay['entitlement_id'], 'refund_sessions': 9})
    print('\n[B4] 退 3 节后再退 9 节 → HTTP %s body=%s' % (resp.status_code, resp.text[:200]))

    assert resp.status_code != 500, '超退走了 500 未映射路径'
    assert 400 <= resp.status_code < 500, '超退应 4xx，实际 %s' % resp.status_code

    with SessionLocal() as db:
        refs_after = db.scalar(select(func.count(RefundRecord.id)).where(
            RefundRecord.payment_id == pay['id']))
        ledgers_after = db.scalar(select(func.count(LedgerEntry.id)).where(
            LedgerEntry.kind == 'REFUND',
            LedgerEntry.ref_id.in_(select(RefundRecord.id).where(
                RefundRecord.payment_id == pay['id']))))
        refunded_after = int(db.get(PaymentRecord, pay['id']).refunded_cents)
    assert refs_after == refs_before, '失败的超退仍写入了退款单'
    assert ledgers_after == ledgers_before, '失败的超退仍写入了流水'
    assert refunded_after == refunded_before, '失败的超退仍扣减了金额'

    # 储值卡按金额超退同样必须 4xx
    stored_product = _api_product(client, tokens['a'], ptype='STORED', sessions=0, price=50000)
    pay2 = _api_pay(client, tokens['a'], store_id=ctx.store,
                    member_id=ctx.factory.member(ctx.tenant),
                    product_id=stored_product, amount_cents=50000)
    over = client.post('/api/payments/refund', headers=tokens['a'],
                       json={'entitlement_id': pay2['entitlement_id'], 'refund_cents': 50001})
    assert over.status_code != 500, '储值超退走了 500'
    assert 400 <= over.status_code < 500, '储值超退应 4xx，实际 %s' % over.status_code


def test_qa_B5_void_rules(client, tokens, ctx):
    """VOID：未消课可作废且列表可见；已消课作废必须 4xx。"""
    # ① 未消课 → 可作废
    income_before = _overview(client, tokens['a'])['month_income_cents']
    mid = ctx.factory.member(ctx.tenant)
    product_id = _api_product(client, tokens['a'], ptype='PACKAGE',
                              sessions=10, price=199900)
    pay = _api_pay(client, tokens['a'], store_id=ctx.store, member_id=mid,
                   product_id=product_id, amount_cents=199900)
    assert _overview(client, tokens['a'])['month_income_cents'] == income_before + 199900

    voided = client.post('/api/payments/%s/void' % pay['id'], headers=tokens['a'],
                         json={'remark': 'QA 验收：录错单'})
    assert voided.status_code == 200, '未消课作废失败：%s' % voided.text
    assert voided.json()['status'] == 'VOID'

    listed = client.get('/api/payments', headers=tokens['a']).json()
    row = next((p for p in listed if p['id'] == pay['id']), None)
    assert row is not None, '作废单在列表里消失了，无法追溯'
    assert row['status'] == 'VOID'
    assert _overview(client, tokens['a'])['month_income_cents'] == income_before, \
        '作废后这笔钱仍计入收入'

    # ② 已消课 → 必须 4xx
    mid2 = ctx.factory.member(ctx.tenant)
    product2 = _api_product(client, tokens['a'], ptype='PACKAGE',
                            sessions=10, price=199900)
    pay2 = _api_pay(client, tokens['a'], store_id=ctx.store, member_id=mid2,
                    product_id=product2, amount_cents=199900)
    with SessionLocal() as db:
        ent = db.get(Entitlement, pay2['entitlement_id'])
        ent.used_sessions = 1
        db.commit()
    resp = client.post('/api/payments/%s/void' % pay2['id'], headers=tokens['a'], json={})
    print('\n[B5] 已消课作废 → HTTP %s' % resp.status_code)
    assert resp.status_code != 500, '已消课作废走了 500'
    assert 400 <= resp.status_code < 500, '已消课作废应 4xx，实际 %s' % resp.status_code
    with SessionLocal() as db:
        assert db.get(PaymentRecord, pay2['id']).status == 'PAID', '拒绝作废后状态被改动'


def test_qa_B6_negative_balance_not_triggered_by_refund(client, tokens, ctx):
    """部分退款后跑账实核对，new_diffs 必须为 0（退款负流水不能算成多消课）。"""
    mid = ctx.factory.member(ctx.tenant)
    product_id = _api_product(client, tokens['a'], ptype='PACKAGE',
                              sessions=10, price=199900)
    pay = _api_pay(client, tokens['a'], store_id=ctx.store, member_id=mid,
                   product_id=product_id, amount_cents=199900)
    refund = client.post('/api/payments/refund', headers=tokens['a'],
                         json={'entitlement_id': pay['entitlement_id'], 'refund_sessions': 4})
    assert refund.status_code == 200, refund.text

    # 预热一次：吸收历史用例可能遗留的其他会员差异（避免环境串扰污染断言）
    warm = client.post('/api/dashboard/reconcile', headers=tokens['a'],
                       json={'store_id': ctx.store, 'date': business_date()})
    assert warm.status_code == 200, warm.text

    second = client.post('/api/dashboard/reconcile', headers=tokens['a'],
                         json={'store_id': ctx.store, 'date': business_date()})
    assert second.status_code == 200, second.text
    body = second.json()
    print('\n[B6] 部分退 4 节后核对：new_diffs=%s purchase=%s refund=%s net=%s'
          % (body['new_diffs'], body['purchase_cents'], body['refund_cents'],
             body['net_income_cents']))

    assert body['new_diffs'] == 0, '退款负流水被误判为负余额：%s' % body
    with SessionLocal() as db:
        mine = db.scalars(select(ReconciliationDiff).where(
            ReconciliationDiff.tenant_id == ctx.tenant,
            ReconciliationDiff.ref_id == mid)).all()
    assert mine == [], '本会员被误挂差异：%s' % [(d.id, d.description) for d in mine]


# =====================================================================
# C. 回归
# =====================================================================
def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env['DATABASE_URL'] = db_url
    exe = str(Path(sys.executable).parent / 'alembic')
    return subprocess.run([exe, *args], cwd=str(REPO_ROOT), env=env,
                          capture_output=True, text=True, timeout=240)


def _temp_db() -> tuple:
    """自建临时库目录。

    不用 pytest 的 ``tmp_path``：本机执行环境对 ``pytest-of-*`` 目录的 mkdir 会抛
    ``FileExistsError``（宿主沙箱的 brokered mkdir 行为），属环境问题、与被测代码无关，
    因此这里用 ``tempfile.mkdtemp`` 自管生命周期。
    """
    workdir = Path(tempfile.mkdtemp(prefix='yoga-qa-'))
    return workdir, workdir / 'qa.db'


def test_qa_C1_migration_upgrade_downgrade_cycle():
    """upgrade head → downgrade -1 ×2 → upgrade head 三步都必须成功。"""
    workdir, db_file = _temp_db()
    db_url = 'sqlite:///' + str(db_file)
    try:
        steps = [('upgrade', 'head'), ('downgrade', '-1'), ('downgrade', '-1'),
                 ('upgrade', 'head')]
        for args in steps:
            got = _alembic(db_url, *args)
            print('\n[C1] alembic %s -> rc=%s' % (' '.join(args), got.returncode))
            if got.returncode != 0:
                print(got.stdout[-2000:])
                print(got.stderr[-2000:])
            assert got.returncode == 0, 'alembic %s 失败：%s' % (' '.join(args),
                                                               got.stderr[-800:])

        current = _alembic(db_url, 'current')
        assert current.returncode == 0
        assert 'b2c8d4e6f012' in current.stdout, 'head 版本不对：%s' % current.stdout

        # 关键结构必须在升降级后依然存在
        conn = sqlite3.connect(str(db_file))
        try:
            cols = [r[1] for r in conn.execute('PRAGMA table_info(payment_records)')]
            assert 'refunded_cents' in cols, '升降级后 refunded_cents 丢失'
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                               "AND name='class_sessions'").fetchone()[0]
            assert 'ck_session_booked_range' in sql, '升降级后 CHECK 约束丢失'
            idx = [r[1] for r in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='enrollments'")]
            assert 'ix_enroll_session_status' in idx, '升降级后 (session,status) 索引丢失'
        finally:
            conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_qa_C2_dirty_data_repair_on_upgrade():
    """脏库升级：capacity=1 却 4 条 BOOKED、booked=1 → 1 BOOKED + 3 WAITLIST + 3 条 DATA_REPAIR。"""
    workdir, db_file = _temp_db()
    db_url = 'sqlite:///' + str(db_file)
    try:
        base = _alembic(db_url, 'upgrade', '7c1e9a2b3d4f')
        assert base.returncode == 0, base.stderr[-800:]
        _seed_dirty_db(db_file)

        upgraded = _alembic(db_url, 'upgrade', 'head')
        print('\n[C2] 脏库升级 rc=%s' % upgraded.returncode)
        if upgraded.returncode != 0:
            print(upgraded.stderr[-2000:])
        assert upgraded.returncode == 0, '脏库升级失败：%s' % upgraded.stderr[-800:]

        conn = sqlite3.connect(str(db_file))
        try:
            booked, capacity, status = conn.execute(
                "SELECT booked, capacity, status FROM class_sessions WHERE id='s-dirty'"
            ).fetchone()
            rows = conn.execute(
                "SELECT id, status, waitlist_seq FROM enrollments WHERE session_id='s-dirty' "
                "ORDER BY created_at ASC").fetchall()
            audits = conn.execute(
                "SELECT resource_id, detail FROM audit_logs WHERE action='DATA_REPAIR'").fetchall()
            active = conn.execute(
                "SELECT COUNT(*) FROM enrollments WHERE session_id='s-dirty' "
                "AND status IN ('BOOKED','OFFERED')").fetchone()[0]
        finally:
            conn.close()

        print('[C2] booked=%s capacity=%s status=%s active=%s rows=%s audits=%d'
              % (booked, capacity, status, active, rows, len(audits)))

        assert active == 1, '修复后仍占 %d 个名额（期望 1）' % active
        assert sum(1 for r in rows if r[1] == 'BOOKED') == 1
        assert sum(1 for r in rows if r[1] == 'WAITLIST') == 3
        assert booked == 1, 'booked=%s（期望 1）' % booked
        assert status == 'FULL', 'status=%s（期望 FULL）' % status
        assert len(audits) == 3, 'DATA_REPAIR 审计 %d 条（期望 3）' % len(audits)
        assert all('超员修复' in a[1] for a in audits), '审计 detail 不可读'
        # 保留的是最早那条（created_at 最小）
        assert rows[0][1] == 'BOOKED', '降级顺序错误：未保留最早的预约'
        # 候补序号接着排，不并列
        assert [r[2] for r in rows[1:]] == [1, 2, 3], \
            '候补序号不连续：%s' % [r[2] for r in rows[1:]]

        # CHECK 兜底：直接写超卖必须被拒绝
        conn = sqlite3.connect(str(db_file))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE class_sessions SET booked=5 WHERE id='s-dirty'")
                conn.commit()
        finally:
            conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _seed_dirty_db(db_file: Path) -> None:
    """造脏数据：capacity=1 的课节里 4 条 BOOKED，booked 被写成 1。"""
    now = '2026-11-01 10:00:00'
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(
            "INSERT INTO tenants (id,code,name,status,contact_name,contact_phone_mask,plan,"
            "created_at,updated_at) VALUES ('t-x','QATX','QA脏数据租户','ACTIVE','','','FREE',?,?)",
            (now, now))
        conn.execute(
            "INSERT INTO class_sessions (id,template_id,store_id,coach_id,course_name,"
            "starts_at,ends_at,capacity,booked,status,cancel_deadline_hours,tenant_id,"
            "created_at,updated_at) VALUES ('s-dirty','tpl','store-x','','QA脏数据团课',"
            "'2026-11-04 10:00:00','2026-11-04 11:00:00',1,1,'OPEN',2,'t-x',?,?)",
            (now, now))
        for i in range(4):
            conn.execute(
                "INSERT INTO members (id,phone_hash,phone_mask,name,source_store_id,status,"
                "note,tenant_id,created_at,updated_at) VALUES (?,?,?,?,'','ACTIVE','','t-x',?,?)",
                ('m%d' % i, 'qa-hash-%d' % i, '139****%04d' % i, 'QA会员%d' % i, now, now))
            conn.execute(
                "INSERT INTO enrollments (id,session_id,member_id,store_id,status,source,"
                "waitlist_seq,cancelled_at,tenant_id,created_at,updated_at,offer_expires_at) "
                "VALUES (?,?,?,'store-x','BOOKED','MEMBER',0,NULL,'t-x',?,?,NULL)",
                ('e%d' % i, 's-dirty', 'm%d' % i,
                 '2026-11-01 10:0%d:00' % i, '2026-11-01 10:0%d:00' % i))
        conn.commit()
    finally:
        conn.close()


def test_qa_C3_main_chain_over_http(client, tokens, ctx, monkeypatch):
    """主链路回归（全 HTTP）：任一步 500 即失败。"""
    admin = tokens['a']
    steps = []

    def record(step: str, resp, expect_ok: bool = True):
        steps.append((step, resp.status_code, resp.text[:300]))
        assert resp.status_code != 500, '步骤「%s」返回 500：%s' % (step, resp.text[:500])
        if expect_ok:
            assert 200 <= resp.status_code < 300, \
                '步骤「%s」失败 %s：%s' % (step, resp.status_code, resp.text[:400])
        return resp

    phone1, phone2 = '13900001111', '13900002222'

    # ① 后台登录（fixture 已完成真实登录，这里显式再走一次确保链路）
    login = record('登录', client.post('/api/auth/login', json={
        'username': 'admin_a', 'password': 'pytest-admin-1234', 'tenant_id': ctx.tenant}))
    admin = {'Authorization': 'Bearer ' + login.json()['access_token']}

    # ② 建档
    member1 = record('建档1', client.post('/api/members', headers=admin, json={
        'phone': phone1, 'name': '链路会员甲'})).json()
    member2 = record('建档2', client.post('/api/members', headers=admin, json={
        'phone': phone2, 'name': '链路会员乙'})).json()

    # ③ 收款（两人各一张 10 节课卡）
    product_id = record('建商品', client.post('/api/payments/products', headers=admin, json={
        'name': '链路课时包', 'ptype': 'PACKAGE', 'total_sessions': 10,
        'price_cents': 199900})).json()['id']
    pay1 = record('收款1', client.post('/api/payments', headers=admin, json={
        'store_id': ctx.store, 'member_id': member1['id'], 'product_id': product_id,
        'amount_cents': 199900, 'method': 'CASH'}))
    pay2 = record('收款2', client.post('/api/payments', headers=admin, json={
        'store_id': ctx.store, 'member_id': member2['id'], 'product_id': product_id,
        'amount_cents': 199900, 'method': 'WECHAT'}))

    # ④ 建模板 + 建课节（容量 1，48 小时后，留出 2h 约课截止余量）
    template = record('建课程模板', client.post('/api/schedule/templates', headers=admin, json={
        'name': '链路团课', 'store_id': ctx.store, 'ctype': 'GROUP', 'capacity': 1})).json()
    starts_at = ((now_naive() + timedelta(hours=48)).replace(microsecond=0)
                 .strftime('%Y-%m-%d %H:%M'))
    session = record('建课节', client.post('/api/schedule/sessions', headers=admin, json={
        'template_id': template['id'], 'starts_at': starts_at,
        'cancel_deadline_hours': 2})).json()
    session_id = session['id']

    # ⑤ 微信 mock 登录 + 绑定到已建档会员（phone 相同 → 绑定同一 member）
    wx = record('微信登录', client.post('/api/auth/wx-login', json={
        'code': 'qa-code-' + uuid.uuid4().hex[:8], 'tenant_id': ctx.tenant}))
    assert wx.json()['need_bind'] is True, '新 openid 应要求绑定'
    bind1 = record('微信绑定1', client.post('/api/auth/wx-bind', json={
        'code': 'qa-bind-' + uuid.uuid4().hex[:8], 'tenant_id': ctx.tenant,
        'phone': phone1, 'name': '链路会员甲'})).json()
    assert bind1['member']['id'] == member1['id'], '绑定未落到已建档会员'
    h1 = {'Authorization': 'Bearer ' + bind1['token']}

    bind2 = record('微信绑定2', client.post('/api/auth/wx-bind', json={
        'code': 'qa-bind-' + uuid.uuid4().hex[:8], 'tenant_id': ctx.tenant,
        'phone': phone2, 'name': '链路会员乙'})).json()
    assert bind2['member']['id'] == member2['id']
    h2 = {'Authorization': 'Bearer ' + bind2['token']}

    # ⑥ 会员端看课程表 + 约课
    record('会员课程表', client.get('/api/app/sessions', headers=h1))
    booked1 = record('约课1', client.post('/api/app/book', headers=h1,
                                          json={'session_id': session_id})).json()
    assert booked1['status'] == 'BOOKED'

    # ⑦ 满员候补
    booked2 = record('约课2（候补）', client.post('/api/app/book', headers=h2,
                                                  json={'session_id': session_id})).json()
    assert booked2['status'] == 'WAITLIST', '满员应进候补，实际 %s' % booked2['status']
    assert booked2['waitlist_seq'] == 1

    # ⑧ 取消 → 递补 OFFERED
    record('取消1', client.post('/api/app/cancel', headers=h1,
                                json={'enrollment_id': booked1['id']}))
    mine2 = record('会员乙预约列表', client.get('/api/app/bookings', headers=h2)).json()
    enr2 = next(e for e in mine2 if e['session_id'] == session_id)
    assert enr2['status'] == 'OFFERED', '取消后未递补，实际 %s' % enr2['status']
    assert enr2['can_confirm'] is True

    # ⑨ 确认递补
    confirmed = record('确认递补', client.post(
        '/api/app/offers/%s/confirm' % enr2['id'], headers=h2)).json()
    assert confirmed['status'] == 'BOOKED'
    snap = _snapshot(session_id)
    assert snap['booked'] == 1 and snap['active'] == 1, '递补后名额口径异常：%s' % snap

    # ⑩ 教练点名：时间窗外必须 4xx
    from app.services import attendance as att_mod
    monkeypatch.setattr(att_mod, 'settings',
                        SimpleNamespace(ATTENDANCE_WINDOW_MINUTES=30))
    outside = client.post('/api/attendance/mark', headers=tokens['coach_a'], json={
        'session_id': session_id, 'member_id': member2['id'], 'status': 'ATTENDED'})
    print('\n[C3] 时间窗外点名 → HTTP %s %s' % (outside.status_code, outside.text[:160]))
    assert outside.status_code != 500, '时间窗外点名走了 500'
    assert 400 <= outside.status_code < 500, \
        '时间窗外点名应 4xx，实际 %s' % outside.status_code

    # 时间窗内 → 成功
    monkeypatch.setattr(att_mod, 'settings',
                        SimpleNamespace(ATTENDANCE_WINDOW_MINUTES=0))
    marked = record('点名消课', client.post('/api/attendance/mark', headers=tokens['coach_a'],
                                            json={'session_id': session_id,
                                                  'member_id': member2['id'],
                                                  'status': 'ATTENDED'}))
    assert marked.json()['status'] == 'ATTENDED'
    snap = _snapshot(session_id)
    assert snap['booked'] == 0, '点名后 booked 未回落：%d' % snap['booked']

    # ⑪ 看板 + 账实核对
    overview = record('看板', client.get('/api/dashboard/overview', headers=admin)).json()
    assert 'month_income_cents' in overview
    recon = record('账实核对', client.post('/api/dashboard/reconcile', headers=admin,
                                           json={'store_id': ctx.store,
                                                 'date': business_date()})).json()
    assert 'new_diffs' in recon

    print('\n[C3] 主链路 %d 步全部通过（无 500）：%s'
          % (len(steps), [(s[0], s[1]) for s in steps]))


def test_qa_C4_rebook_same_session_three_times(client, member_token, ctx):
    """约课 → 取消 → 再约同一节，连续 3 轮都不得 409，记录复用（仅 1 条）。"""
    member_id, headers = member_token(ctx.tenant)
    ctx.factory.entitlement(ctx.tenant, member_id, total_sessions=10)
    session_id = ctx.session(capacity=1, hours_ahead=240)

    first_id = None
    for attempt in range(1, 4):
        resp = client.post('/api/app/book', headers=headers, json={'session_id': session_id})
        print('\n[C4] 第 %d 次约课 → HTTP %s %s' % (attempt, resp.status_code, resp.text[:160]))
        assert resp.status_code == 200, '第 %d 次约课失败 %s（409=取消记录撞唯一约束）' \
            % (attempt, resp.status_code)
        body = resp.json()
        assert body['status'] == 'BOOKED'
        if first_id is None:
            first_id = body['id']
        else:
            assert body['id'] == first_id, '第 %d 次约课新建了记录，未复用取消记录' % attempt
        assert _snapshot(session_id)['booked'] == 1

        cancelled = client.post('/api/app/cancel', headers=headers,
                                json={'enrollment_id': body['id']})
        assert cancelled.status_code == 200, cancelled.text
        assert _snapshot(session_id)['booked'] == 0

    with SessionLocal() as db:
        total = db.scalar(select(func.count(Enrollment.id)).where(
            Enrollment.session_id == session_id, Enrollment.member_id == member_id))
    assert total == 1, '同一会员同一课节存在 %d 条记录（应复用为 1 条）' % total


def test_qa_C5_attendance_recounts_booked(client, tokens, ctx):
    """容量 2 约满 → 点名 1 人 → booked==1 且 OPEN → 点名第 2 人 → booked==0。"""
    session_id = ctx.session(capacity=2, hours_ahead=250)
    m1, m2 = [ctx.member_with_card(5) for _ in range(2)]
    e1 = _book_once(m1, session_id)
    e2 = _book_once(m2, session_id)
    assert e1['status'] == e2['status'] == 'BOOKED'
    assert _snapshot(session_id)['booked'] == 2
    assert _snapshot(session_id)['status'] == 'FULL'

    coach = tokens['coach_a']
    r1 = client.post('/api/attendance/mark', headers=coach, json={
        'session_id': session_id, 'member_id': m1, 'status': 'ATTENDED'})
    assert r1.status_code == 200, r1.text
    snap = _snapshot(session_id)
    print('\n[C5] 点名 1 人后：%s' % snap)
    assert snap['booked'] == 1, '点名 1 人后 booked=%d（期望 1）' % snap['booked']
    assert snap['active'] == snap['booked']
    assert snap['status'] == 'OPEN', '有人退出占位后应回 OPEN，实际 %s' % snap['status']

    r2 = client.post('/api/attendance/mark', headers=coach, json={
        'session_id': session_id, 'member_id': m2, 'status': 'ATTENDED'})
    assert r2.status_code == 200, r2.text
    snap = _snapshot(session_id)
    print('[C5] 点名 2 人后：%s' % snap)
    assert snap['booked'] == 0 and snap['active'] == 0


def test_qa_C6_permissions(client, tokens):
    """教练访问 /api/tenant/users 必须 403；未登录 /api/app/me 必须 401。"""
    coach = client.get('/api/tenant/users', headers=tokens['coach_a'])
    print('\n[C6] 教练访问 /api/tenant/users → %s' % coach.status_code)
    assert coach.status_code == 403, '教练越权拿到用户列表：%s' % coach.status_code

    anon = client.get('/api/app/me')
    assert anon.status_code == 401, '未登录访问 /api/app/me 返回 %s（期望 401）' % anon.status_code

    # 会员令牌访问后台接口也必须被拒
    bad = client.get('/api/payments', headers={'Authorization': 'Bearer not-a-token'})
    assert bad.status_code == 401, '伪造令牌返回 %s（期望 401）' % bad.status_code


# =====================================================================
# D. 质量
# =====================================================================
def test_qa_D1_malformed_datetime_must_not_500(client, tokens, ctx):
    """非法时间入参必须 4xx：服务层 parse_dt 抛的是原生 ValueError，不在 AppError 体系内。

    若未映射，全局异常处理会统一兜成 500（`app/main.py:69` 的 `Exception` handler），
    客户端无法区分「自己传错」和「服务端炸了」。

    注意：TestClient 默认 `raise_server_exceptions=True`，未捕获异常会直接在测试里抛出
    而不是返回 500，因此这里必须显式关闭它，才能拿到「真实 HTTP 状态码」这一证据。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    member = ctx.factory.member(ctx.tenant)
    product = _api_product(client, tokens['a'], ptype='PACKAGE', sessions=10, price=199900)
    template = client.post('/api/schedule/templates', headers=tokens['a'], json={
        'name': 'QA时间格式模板', 'store_id': ctx.store, 'capacity': 5}).json()

    probes = [
        ('POST /api/payments（paid_at=2026/13/45）', 'POST', '/api/payments', {
            'store_id': ctx.store, 'member_id': member, 'product_id': product,
            'amount_cents': 199900, 'paid_at': '2026/13/45'}),
        ('POST /api/schedule/sessions（starts_at=明天上午）', 'POST', '/api/schedule/sessions', {
            'template_id': template['id'], 'starts_at': '明天上午十点'}),
        ('GET /api/schedule/sessions（date=not-a-date）', 'GET',
         '/api/schedule/sessions?date=not-a-date', None),
        ('POST /api/dashboard/reconcile（date=2026-13-45）', 'POST',
         '/api/dashboard/reconcile', {'store_id': ctx.store, 'date': '2026-13-45'}),
        ('POST /api/schedule/weekly（week_start=xxxx）', 'POST', '/api/schedule/weekly', {
            'template_id': template['id'], 'week_start': 'xxxx', 'times': ['09:00']}),
        ('GET /api/app/sessions（date=not-a-date）', 'GET',
         '/api/app/sessions?date=not-a-date', None),
        ('POST /api/change-requests（new_starts_at 非法）', 'POST', '/api/change-requests', {
            'session_id': 'any', 'change_type': 'RESCHEDULE',
            'new_starts_at': '2026年11月1日'}),
    ]

    bad, observed = [], []
    member_headers = None
    with TestClient(app, raise_server_exceptions=False) as raw:
        # 会员端令牌（用于 /api/app/sessions 的 500 探测）
        bind = raw.post('/api/auth/wx-bind', json={
            'code': 'qa-d1-' + uuid.uuid4().hex[:8], 'tenant_id': ctx.tenant,
            'phone': '1390000%04d' % (uuid.uuid4().int % 10000), 'name': 'D1探测会员'})
        if bind.status_code == 200:
            member_headers = {'Authorization': 'Bearer ' + bind.json()['token']}

        for name, method, url, body in probes:
            headers = tokens['a']
            if url.startswith('/api/app/'):
                headers = member_headers or tokens['a']
            resp = (raw.post(url, headers=headers, json=body) if method == 'POST'
                    else raw.get(url, headers=headers))
            observed.append((name, resp.status_code))
            print('\n[D1] %s → HTTP %s' % (name, resp.status_code))
            if resp.status_code >= 500:
                bad.append({'probe': name, 'status': resp.status_code,
                            'body': resp.text[:160]})

    print('[D1] 汇总：%s' % observed)
    assert not bad, '存在未映射的 500 路径（非法入参应 4xx）：%s' % bad
