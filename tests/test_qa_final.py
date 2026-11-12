"""QA 最终复验（D1 入参 4xx + D4-R2 收款并发幂等 + 合法输入不误杀 + 补漏扫描）。

**独立性声明**：不复用 `tests/test_input_validation.py`、`tests/test_payment_concurrency.py`
的任何断言或辅助函数；也不修改 `app/` 下的源码。全部自行造数据、自行断言。

为什么必须用 `TestClient(app, raise_server_exceptions=False)`：
默认 `raise_server_exceptions=True` 时，未捕获异常会直接在测试里抛出而不是返回 500，
于是「有没有 500」这件事根本观测不到 —— 这正是本项复验的核心证据。
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import LedgerEntry, PaymentRecord
from app.utils.timeutil import now_naive

TENANT = 't-a'
STORE = 'store-a'


# ---------------------------------------------------------------------
# 自建辅助
# ---------------------------------------------------------------------
def _raw() -> TestClient:
    """能观测真实 500 的客户端。"""
    return TestClient(app, raise_server_exceptions=False)


def _assert_error_envelope(resp, label: str) -> None:
    """统一错误体：{error: {code, message, request_id}}。"""
    body = resp.json()
    assert 'error' in body, '%s 错误体缺 error 字段：%s' % (label, body)
    err = body['error']
    for field in ('code', 'message', 'request_id'):
        assert field in err and err[field], '%s 错误体缺 %s：%s' % (label, field, err)


# =====================================================================
# 1. D1：七个入参入口全部 4xx、无 500、错误体统一
# =====================================================================
def test_final_d1_seven_entries_return_4xx(world, tokens):
    factory = world['factory']
    member_id = factory.member(TENANT)
    product_id = factory.product(TENANT, 'PACKAGE', 10, 100000)
    template_id = factory.template(TENANT, STORE, capacity=5)
    live_session = factory.session(TENANT, store_id=STORE, capacity=4, hours_ahead=210)

    bad_dt, bad_date, bad_month = '明天上午十点', '2026/13/45', '2026-13-45'

    probes = [
        ('① POST /api/payments (paid_at)', 'POST', '/api/payments', tokens['a'], {
            'store_id': STORE, 'member_id': member_id, 'product_id': product_id,
            'amount_cents': 100000, 'method': 'CASH', 'paid_at': bad_date}),
        ('② POST /api/schedule/sessions (starts_at)', 'POST', '/api/schedule/sessions',
         tokens['a'], {'template_id': template_id, 'starts_at': bad_dt}),
        ('③ GET /api/schedule/sessions (date)', 'GET',
         '/api/schedule/sessions?date=not-a-date', tokens['a'], None),
        ('④ POST /api/dashboard/reconcile (date)', 'POST', '/api/dashboard/reconcile',
         tokens['a'], {'store_id': STORE, 'date': bad_month}),
        ('⑥ POST /api/change-requests (new_starts_at)', 'POST', '/api/change-requests',
         tokens['a'], {'session_id': live_session, 'change_type': 'RESCHEDULE',
                       'new_starts_at': bad_dt, 'reason': 'QA 复验'}),
        ('⑦ POST /api/schedule/weekly (week_start)', 'POST', '/api/schedule/weekly',
         tokens['a'], {'template_id': template_id, 'week_start': bad_month,
                       'times': ['09:00']}),
    ]

    results, problems = [], []
    with _raw() as raw:
        # ⑤ 会员端课程表（需会员令牌，走真实 wx-bind 链路）
        bind = raw.post('/api/auth/wx-bind', json={
            'code': 'qa-final-' + uuid.uuid4().hex[:10], 'tenant_id': TENANT,
            'phone': '1370000%04d' % (uuid.uuid4().int % 10000), 'name': '复验会员'})
        assert bind.status_code == 200, bind.text
        member_headers = {'Authorization': 'Bearer ' + bind.json()['token']}
        probes.append(('⑤ GET /api/app/sessions (date)', 'GET',
                       '/api/app/sessions?date=not-a-date', member_headers, None))

        for label, method, url, headers, body in probes:
            resp = (raw.post(url, headers=headers, json=body) if method == 'POST'
                    else raw.get(url, headers=headers))
            results.append((label, resp.status_code, resp.text[:150]))
            if resp.status_code == 500:
                problems.append('%s → 500（仍走兜底 handler）' % label)
                continue
            if not (400 <= resp.status_code < 500):
                problems.append('%s → %s（期望 4xx）' % (label, resp.status_code))
                continue
            try:
                _assert_error_envelope(resp, label)
            except AssertionError as exc:
                problems.append(str(exc))

    print('\n[D1 复验] 七个入口实测：')
    for label, code, snippet in results:
        print('  %-46s → HTTP %-3s %s' % (label, code, snippet))

    assert not problems, '入参 4xx 复验不达标：%s' % problems
    assert len(results) == 7, '应为 7 个入口，实际 %d' % len(results)


# =====================================================================
# 2. D4-R2：同幂等键并发收款 —— 3 轮
# =====================================================================
ROUNDS = 3


def test_final_r2_concurrent_payment_idempotent_3_rounds(world, tokens):
    """2 线程 + 同一 idempotency_key 并发 POST /api/payments，独立跑 3 轮。

    每轮断言：恰 1 张收款单、恰 1 条 PURCHASE 流水、两响应非 500。
    """
    factory = world['factory']
    failures, rounds_log = [], []

    with _raw() as raw:
        for rnd in range(1, ROUNDS + 1):
            member_id = factory.member(TENANT)
            product_id = factory.product(TENANT, 'PACKAGE', 10, 120000)
            key = 'qa-r2-%s-%d' % (uuid.uuid4().hex[:10], rnd)
            payload = {'store_id': STORE, 'member_id': member_id,
                       'product_id': product_id, 'amount_cents': 120000,
                       'method': 'WECHAT', 'idempotency_key': key}

            sink, barrier = [], threading.Barrier(2, timeout=30)

            def worker() -> None:
                try:
                    barrier.wait()
                    resp = raw.post('/api/payments', headers=tokens['a'], json=payload)
                    sink.append((resp.status_code, resp.text[:200]))
                except BaseException as exc:  # noqa: BLE001 - 汇总后统一断言
                    sink.append((-1, '%s: %s' % (type(exc).__name__, exc)))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=120)
            alive = [t for t in threads if t.is_alive()]

            with SessionLocal() as db:
                pays = db.scalars(select(PaymentRecord).where(
                    PaymentRecord.tenant_id == TENANT,
                    PaymentRecord.idempotency_key == key)).all()
                purchase = db.scalar(select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.tenant_id == TENANT,
                    LedgerEntry.idempotency_key == key,
                    LedgerEntry.kind == 'PURCHASE')) or 0

            codes = [c for c, _ in sink]
            rounds_log.append({'round': rnd, 'codes': codes,
                               'pays': len(pays), 'purchase': purchase,
                               'responses': [t for _, t in sink]})

            if alive:
                failures.append('第 %d 轮线程未结束' % rnd)
            if len(sink) != 2:
                failures.append('第 %d 轮只有 %d 个响应' % (rnd, len(sink)))
            if any(c == 500 or c == -1 for c in codes):
                failures.append('第 %d 轮出现 500/线程异常：%s' % (rnd, sink))
            if len(pays) != 1:
                failures.append('第 %d 轮写了 %d 张收款单' % (rnd, len(pays)))
            if purchase != 1:
                failures.append('第 %d 轮写了 %d 条 PURCHASE 流水' % (rnd, purchase))
            if 200 not in codes:
                failures.append('第 %d 轮两线程都没成功收款：%s' % (rnd, codes))

    print('\n[R2 复验] 同幂等键并发收款（3 轮 × 2 线程）：')
    for row in rounds_log:
        print('  R%(round)02d codes=%(codes)s 收款单=%(pays)d PURCHASE流水=%(purchase)d'
              % row)
        for text in row['responses']:
            print('       resp: %s' % text.replace('\n', ' ')[:140])

    assert not failures, '并发收款幂等复验不达标：%s' % failures


# =====================================================================
# 3. 合法时间输入不得被误杀（三种格式 × HTTP 全链路）
# =====================================================================
def test_final_legal_datetime_not_rejected(world, tokens):
    """YYYY-MM-DD / YYYY-MM-DD HH:MM / ISO8601(T) 三种格式既要能解析，也要能走通建课。"""
    from app.utils.timeutil import parse_dt

    # 单元级：三种格式都能解析出预期值
    assert parse_dt('2026-11-20').strftime('%Y-%m-%d') == '2026-11-20'
    assert parse_dt('2026-11-20 09:30').strftime('%Y-%m-%d %H:%M') == '2026-11-20 09:30'
    assert parse_dt('2026-11-20T09:30').strftime('%Y-%m-%d %H:%M') == '2026-11-20 09:30'
    assert parse_dt('2026-11-20T09:30:15').strftime('%H:%M:%S') == '09:30:15'

    # HTTP 级：三种格式都能建出课节（合法输入不能被新校验误杀）
    factory = world['factory']
    template_id = factory.template(TENANT, STORE, capacity=8)
    day = (now_naive() + timedelta(days=3)).date()
    samples = [
        day.strftime('%Y-%m-%d'),
        (day.strftime('%Y-%m-%d') + ' 09:30'),
        (day.strftime('%Y-%m-%d') + 'T10:15'),
    ]
    created = []
    with _raw() as raw:
        for value in samples:
            resp = raw.post('/api/schedule/sessions', headers=tokens['a'],
                            json={'template_id': template_id, 'starts_at': value})
            created.append((value, resp.status_code, resp.text[:120]))
            assert resp.status_code == 200, \
                '合法时间 %r 被误杀：%s %s' % (value, resp.status_code, resp.text)

        # 排期查询用合法日期同样不能 4xx
        listed = raw.get('/api/schedule/sessions?date=%s' % day.strftime('%Y-%m-%d'),
                         headers=tokens['a'])
        assert listed.status_code == 200, '合法 date 查询被拒：%s' % listed.text

    print('\n[合法输入] 三种格式建课结果：%s' % created)


# =====================================================================
# 4. 补漏扫描：其他用户可控入参不得兜底成 500
# =====================================================================
def test_final_no_500_on_suspect_user_inputs(world, tokens):
    """只扫「用户可控入参」路径（不做全量审计）：期望 2xx/4xx，绝不允许 500。"""
    factory = world['factory']
    member_id = factory.member(TENANT)
    product_id = factory.product(TENANT, 'PACKAGE', 5, 90000)
    template_id = factory.template(TENANT, STORE, capacity=5)
    live_session = factory.session(TENANT, store_id=STORE, capacity=4, hours_ahead=220)

    cases = [
        ('趋势 days=0', 'GET', '/api/dashboard/trend?days=0', None),
        ('趋势 days=999', 'GET', '/api/dashboard/trend?days=999', None),
        ('趋势 days=abc', 'GET', '/api/dashboard/trend?days=abc', None),
        ('收款列表 status 乱填', 'GET', '/api/payments?status=BOGUS', None),
        ('差异列表 status 乱填', 'GET', '/api/dashboard/diffs?status=BOGUS', None),
        ('排期 store_id 不存在', 'GET', '/api/schedule/sessions?store_id=nope', None),
        ('约课截止小时为负', 'POST', '/api/schedule/sessions',
         {'template_id': template_id, 'starts_at': (now_naive() + timedelta(days=5))
          .strftime('%Y-%m-%d %H:%M'), 'cancel_deadline_hours': -3}),
        ('批量排期 times=["25:00"]', 'POST', '/api/schedule/weekly',
         {'template_id': template_id, 'week_start': (now_naive() + timedelta(days=6))
          .strftime('%Y-%m-%d'), 'times': ['25:00']}),
        ('批量排期 times=["abc"]', 'POST', '/api/schedule/weekly',
         {'template_id': template_id, 'week_start': (now_naive() + timedelta(days=6))
          .strftime('%Y-%m-%d'), 'times': ['abc']}),
        ('点名字段乱填', 'POST', '/api/attendance/mark',
         {'session_id': live_session, 'member_id': member_id, 'status': 'WHATEVER'}),
        ('确认不存在的递补', 'POST', '/api/app/offers/not-exist/confirm', None),
        ('收款金额与售价不符', 'POST', '/api/payments',
         {'store_id': STORE, 'member_id': member_id, 'product_id': product_id,
          'amount_cents': 1}),
        ('收款金额为负', 'POST', '/api/payments',
         {'store_id': STORE, 'member_id': member_id, 'product_id': product_id,
          'amount_cents': -100}),
        ('手机号过短', 'POST', '/api/members', {'phone': '123', 'name': '短号'}),
        ('空用户名登录', 'POST', '/api/auth/login', {'username': '', 'password': ''}),
        ('调课 session 不存在', 'POST', '/api/change-requests',
         {'session_id': 'nope', 'change_type': 'RESCHEDULE',
          'new_starts_at': '2026-12-01 10:00'}),
        ('调课类型非法', 'POST', '/api/change-requests',
         {'session_id': live_session, 'change_type': 'WHATEVER',
          'new_starts_at': '2026-12-01 10:00'}),
        ('权益查询不存在的会员', 'GET', '/api/payments/entitlements/nope', None),
        ('会员详情不存在', 'GET', '/api/members/nope', None),
        ('课节详情不存在', 'GET', '/api/schedule/sessions/nope', None),
    ]

    bad, observed = [], []
    with _raw() as raw:
        for label, method, url, body in cases:
            resp = (raw.post(url, headers=tokens['a'], json=body) if method == 'POST'
                    else raw.get(url, headers=tokens['a']))
            observed.append((label, resp.status_code))
            if resp.status_code >= 500:
                bad.append({'case': label, 'status': resp.status_code,
                            'body': resp.text[:160]})

    print('\n[补漏扫描] %d 条用户可控入参：%s' % (len(observed), observed))
    assert not bad, '仍有用户可控入参兜底成 500：%s' % bad
