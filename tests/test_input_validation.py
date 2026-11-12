"""入参健壮性（D1）：非法日期必须收敛成 4xx，不能落到 main.py 的兜底 handler 变成 500。

根因只有一处 —— ``app/utils/timeutil.parse_dt`` 曾抛原生 ``ValueError``；本文件把
所有会解析日期的对外入口参数化跑一遍，锁住「4xx 而不是 500」这个契约。
"""
from __future__ import annotations

import pytest

CASES = (
    'pay_paid_at',
    'session_starts_at',
    'weekly_week_start',
    'session_list_date',
    'reconcile_date',
    'app_sessions_date',
    'change_request_new_starts_at',
)

BAD_DATETIME = '明天上午十点'
BAD_DATE = '2026/13/45'
BAD_MONTH = '2026-13-45'


def _call(case: str, client, world, tokens, member_token):
    factory = world['factory']
    tenant_a = world['tenant_a']
    if case == 'pay_paid_at':
        member_id = factory.member(tenant_a)
        product_id = factory.product(tenant_a, 'PACKAGE', 10, 100000)
        return client.post('/api/payments', headers=tokens['a'], json={
            'store_id': world['store_a'], 'member_id': member_id,
            'product_id': product_id, 'amount_cents': 100000,
            'method': 'CASH', 'paid_at': BAD_DATE})

    if case == 'session_starts_at':
        template_id = factory.template(tenant_a, world['store_a'])
        return client.post('/api/schedule/sessions', headers=tokens['a'], json={
            'template_id': template_id, 'starts_at': BAD_DATETIME})

    if case == 'weekly_week_start':
        template_id = factory.template(tenant_a, world['store_a'])
        return client.post('/api/schedule/weekly', headers=tokens['a'], json={
            'template_id': template_id, 'week_start': BAD_MONTH, 'times': ['09:00']})

    if case == 'session_list_date':
        return client.get('/api/schedule/sessions', headers=tokens['a'],
                          params={'date': 'not-a-date'})

    if case == 'reconcile_date':
        return client.post('/api/dashboard/reconcile', headers=tokens['a'], json={
            'store_id': world['store_a'], 'date': BAD_MONTH})

    if case == 'app_sessions_date':
        _, headers = member_token(name='入参校验会员')
        return client.get('/api/app/sessions', headers=headers,
                          params={'date': 'not-a-date'})

    if case == 'change_request_new_starts_at':
        session_id = factory.session(tenant_a, capacity=4, hours_ahead=210)
        return client.post('/api/change-requests', headers=tokens['a'], json={
            'session_id': session_id, 'change_type': 'RESCHEDULE',
            'new_starts_at': BAD_DATETIME, 'reason': '教练临时有事'})

    raise AssertionError('未覆盖的用例：%s' % case)


@pytest.mark.parametrize('case', CASES)
def test_invalid_datetime_returns_4xx(case, client, world, tokens, member_token):
    """非法日期 → 400/422，且错误结构走统一错误体系（不是 500，也不是裸堆栈）。"""
    resp = _call(case, client, world, tokens, member_token)
    assert resp.status_code != 500, '%s 仍返回 500：%s' % (case, resp.text)
    assert resp.status_code in (400, 422), \
        '%s 状态码应为 400/422，实际 %s：%s' % (case, resp.status_code, resp.text)
    body = resp.json()
    assert 'error' in body and body['error']['code']
    assert body['error']['code'] in ('VALIDATION_ERROR', 'BUSINESS_RULE_VIOLATION')


def test_parse_dt_raises_validation_error():
    """根因回归：parse_dt 抛的是 AppError 子类，而不是原生 ValueError。"""
    from app.errors import AppError
    from app.utils.timeutil import parse_dt

    with pytest.raises(AppError) as info:
        parse_dt('2026/13/45')
    assert info.value.http_status == 400
    assert '时间格式不合法' in info.value.message

    # 合法格式保持原行为（含 ISO8601 T 分隔与纯日期）
    assert parse_dt('2026-11-12 09:30').strftime('%Y-%m-%d %H:%M') == '2026-11-12 09:30'
    assert parse_dt('2026-11-12T09:30').strftime('%H:%M') == '09:30'
    assert parse_dt('2026-11-12').strftime('%Y-%m-%d') == '2026-11-12'
