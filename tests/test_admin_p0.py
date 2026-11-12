"""P0 后台自助（T-24/26/28/29/30）+ 入参校验补漏回归。

覆盖三件事：
1. **入参边界**：``times=["25:00"]`` / ``["09:99"]``、``cancel_deadline_hours`` 越界
   必须 4xx，且数据库里**不能**留下半批课节（幽灵课节比报错更难排查）；
2. **后台写口**：门店更新/停用、账号更新/停用、模板更新，含最后一位馆主保护与自我停用保护；
3. **契约**：按周生成幂等（重复提交 created=0）、教练对写接口 403 但可只读排课。
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import ClassSession, Store, User

WEEK = '2030-03-04'  # 远期周一，避免与「今天及未来」类查询互相干扰


# ---------- 工具 ----------
def _count_sessions(tenant_id: str, template_id: str) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count(ClassSession.id)).where(
            ClassSession.tenant_id == tenant_id,
            ClassSession.template_id == template_id)) or 0)


def _weekly(client, headers, template_id: str, times=None, hours=2, week=WEEK):
    return client.post('/api/schedule/weekly', headers=headers, json={
        'template_id': template_id, 'week_start': week,
        'times': times if times is not None else ['09:00'],
        'cancel_deadline_hours': hours})


def _find_user(client, headers, username: str) -> dict:
    rows = client.get('/api/tenant/users', headers=headers).json()
    return next(u for u in rows if u['username'] == username)


def _make_store(client, headers, name: str, code: str) -> str:
    resp = client.post('/api/tenant/stores', headers=headers, json={'name': name, 'code': code})
    assert resp.status_code == 200, resp.text
    return resp.json()['id']


# ==================== 一、入参校验补漏 ====================
@pytest.mark.parametrize('bad_times', [['25:00'], ['09:99'], ['24:00'], ['09:60'], ['0900'], ['9:0:0']])
def test_weekly_rejects_out_of_range_time(bad_times, client, world, tokens):
    """溢出时段会静默进位（25:00→次日 01:00、09:99→10:39），必须整批拒绝。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = _weekly(client, tokens['a'], template_id, times=bad_times)
    assert resp.status_code in (400, 422), resp.text
    body = resp.json()
    assert body['error']['code'] in ('VALIDATION_ERROR', 'BUSINESS_RULE_VIOLATION')
    assert '时段' in body['error']['message']
    assert _count_sessions(world['tenant_a'], template_id) == 0, '非法时段不得落库'


@pytest.mark.parametrize('hours', [-3, 73, 100, -1])
def test_weekly_rejects_invalid_deadline(hours, client, world, tokens):
    """负数截止点会把可约/可取消窗口推到开课之后，必须拒绝。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = _weekly(client, tokens['a'], template_id, times=['09:00'], hours=hours)
    assert resp.status_code in (400, 422), resp.text
    assert '截止小时' in resp.json()['error']['message']
    assert _count_sessions(world['tenant_a'], template_id) == 0


@pytest.mark.parametrize('hours', [-3, 100])
def test_create_session_rejects_invalid_deadline(hours, client, world, tokens):
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.post('/api/schedule/sessions', headers=tokens['a'], json={
        'template_id': template_id, 'starts_at': '2030-03-05 09:00',
        'cancel_deadline_hours': hours})
    assert resp.status_code in (400, 422), resp.text
    assert '截止小时' in resp.json()['error']['message']
    assert _count_sessions(world['tenant_a'], template_id) == 0


@pytest.mark.parametrize('hours', [0, 2, 72])
def test_deadline_hours_boundaries_accepted(hours, client, world, tokens):
    """边界值必须仍然可写：0 = 开课前一直可约，72 = 三天前关闭。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.post('/api/schedule/sessions', headers=tokens['a'], json={
        'template_id': template_id, 'starts_at': '2030-03-05 09:00',
        'cancel_deadline_hours': hours})
    assert resp.status_code == 200, resp.text
    with SessionLocal() as db:
        session_id = resp.json()['id']
        assert db.get(ClassSession, session_id).cancel_deadline_hours == hours


# ==================== 二、按周生成（T-30 后端） ====================
def test_weekly_generates_14_and_is_idempotent(client, world, tokens):
    """7 天 × 2 时段 = 14 节；同周重复提交 created=0、skipped=14。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    first = _weekly(client, tokens['a'], template_id, times=['09:00', '19:30'])
    assert first.status_code == 200, first.text
    assert first.json()['created'] == 14
    assert first.json()['skipped'] == 0

    second = _weekly(client, tokens['a'], template_id, times=['09:00', '19:30'])
    assert second.status_code == 200, second.text
    assert second.json()['created'] == 0
    assert second.json()['skipped'] == 14
    assert _count_sessions(world['tenant_a'], template_id) == 14


def test_weekly_dedupes_repeated_times_in_one_request(client, world, tokens):
    """同一请求里重复时段只算一次（会话 autoflush=False，不先去重会重复建课）。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = _weekly(client, tokens['a'], template_id, times=['09:00', '09:00'])
    assert resp.status_code == 200, resp.text
    assert resp.json()['created'] == 7
    assert _count_sessions(world['tenant_a'], template_id) == 7


# ==================== 三、门店管理（T-24 / T-25） ====================
def test_store_update_fields(client, world, tokens):
    store_id = _make_store(client, tokens['a'], '新建门店甲', 'TS-A1')
    resp = client.put('/api/tenant/stores/' + store_id, headers=tokens['a'], json={
        'name': '门店甲（改）', 'address': '望京 SOHO T1',
        'business_hours': '10:00-22:00', 'phone_mask': '010-****8888'})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['name'] == '门店甲（改）'
    assert body['business_hours'] == '10:00-22:00'
    assert body['status'] == 'ACTIVE'
    # 列表接口带 status，前端据此展示启用/停用
    listed = client.get('/api/tenant/stores', headers=tokens['a']).json()
    assert next(s for s in listed if s['id'] == store_id)['status'] == 'ACTIVE'
    # 空名称仍要拦下
    assert client.put('/api/tenant/stores/' + store_id, headers=tokens['a'],
                      json={'name': '  '}).status_code == 400


def test_store_update_cross_tenant_404(client, world, tokens):
    """跨租户与不存在统一 404，不泄露其他租户数据是否存在。"""
    resp = client.put('/api/tenant/stores/' + world['store_b'], headers=tokens['a'],
                      json={'name': '越权改名'})
    assert resp.status_code == 404, resp.text
    assert client.post('/api/tenant/stores/store-not-exist/disable',
                       headers=tokens['a']).status_code == 404


def test_store_disable_blocked_by_future_sessions(client, world, tokens):
    """仍有未结束课节 → 422 并提示节数；全部取消后可停用。"""
    store_id = _make_store(client, tokens['a'], '待停用门店', 'TS-A2')
    template_id = world['factory'].template(world['tenant_a'], store_id)
    assert _weekly(client, tokens['a'], template_id, times=['09:00']).json()['created'] == 7

    resp = client.post('/api/tenant/stores/%s/disable' % store_id, headers=tokens['a'])
    assert resp.status_code == 422, resp.text
    assert '还有 7 节未结束课程' in resp.json()['error']['message']

    for s in client.get('/api/schedule/sessions', headers=tokens['a'],
                        params={'store_id': store_id}).json():
        client.post('/api/schedule/sessions/%s/cancel' % s['id'], headers=tokens['a'])

    ok = client.post('/api/tenant/stores/%s/disable' % store_id, headers=tokens['a'])
    assert ok.status_code == 200, ok.text
    assert ok.json()['status'] == 'DISABLED'
    with SessionLocal() as db:
        assert db.get(Store, store_id).status == 'DISABLED'


# ==================== 四、账号与角色（T-24 / T-26） ====================
def test_user_update_and_role_change(client, world, tokens):
    resp = client.post('/api/tenant/users', headers=tokens['a'], json={
        'username': 'mgr_t24', 'password': '123456', 'real_name': '王店长',
        'role': 'STORE_MANAGER', 'store_id': world['store_a']})
    assert resp.status_code == 200, resp.text
    user_id = resp.json()['id']

    patched = client.patch('/api/tenant/users/' + user_id, headers=tokens['a'], json={
        'real_name': '王店长（改）', 'role': 'COACH'})
    assert patched.status_code == 200, patched.text
    assert patched.json()['real_name'] == '王店长（改）'
    assert patched.json()['role'] == 'COACH'
    assert patched.json()['status'] == 'ACTIVE'

    # 跨租户 → 404
    assert client.patch('/api/tenant/users/user-admin-b', headers=tokens['a'],
                        json={'real_name': '越权'}).status_code == 404
    # 非法角色 → 400
    assert client.patch('/api/tenant/users/' + user_id, headers=tokens['a'],
                        json={'role': 'ROOT'}).status_code == 400


def test_last_admin_cannot_be_demoted_or_disabled(client, world, tokens):
    """最后一位馆主降级/停用会直接把租户锁死，必须 422。"""
    admin_row = _find_user(client, tokens['a'], 'admin_a')
    demote = client.patch('/api/tenant/users/' + admin_row['id'], headers=tokens['a'],
                          json={'role': 'STORE_MANAGER'})
    assert demote.status_code == 422, demote.text
    assert '馆主' in demote.json()['error']['message']

    disable = client.post('/api/tenant/users/%s/disable' % admin_row['id'], headers=tokens['a'])
    assert disable.status_code == 422, disable.text
    assert '自己' in disable.json()['error']['message']


def test_disable_other_user_and_self_guard(client, world, tokens):
    resp = client.post('/api/tenant/users', headers=tokens['a'], json={
        'username': 'coach_t24', 'password': '123456', 'real_name': '李教练',
        'role': 'COACH', 'store_id': world['store_a']})
    user_id = resp.json()['id']

    ok = client.post('/api/tenant/users/%s/disable' % user_id, headers=tokens['a'])
    assert ok.status_code == 200, ok.text
    assert ok.json()['status'] == 'DISABLED'
    with SessionLocal() as db:
        assert db.get(User, user_id).status == 'DISABLED'
    assert _find_user(client, tokens['a'], 'coach_t24')['status'] == 'DISABLED'


# ==================== 五、课程模板更新（T-29） ====================
def test_template_update_reports_affected_future_sessions(client, world, tokens):
    """改模板只影响之后创建的课节：未来课节数要回报，存量课节保持快照。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'], capacity=8)
    _weekly(client, tokens['a'], template_id, times=['09:00', '19:30'])

    resp = client.patch('/api/schedule/templates/' + template_id, headers=tokens['a'], json={
        'name': '流瑜伽 75', 'capacity': 20, 'duration_minutes': 75,
        'ctype': 'GROUP', 'allowed_ptypes': 'PACKAGE,STORED'})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['name'] == '流瑜伽 75'
    assert body['capacity'] == 20
    assert body['duration_minutes'] == 75
    assert body['allowed_ptypes'] == 'PACKAGE,STORED'
    assert body['affected_future_sessions'] == 14

    sessions = [s for s in client.get('/api/schedule/sessions', headers=tokens['a']).json()
                if s['course_name'] in ('测试课程' + template_id[:4], '流瑜伽 75')]
    # 存量课节快照不变（名称与容量都还是创建时的值）
    assert sessions and all(s['capacity'] == 8 for s in sessions)

    # 新排期才吃新模板
    new = _weekly(client, tokens['a'], template_id, times=['09:00'], week='2030-03-11')
    assert new.json()['created'] == 7
    fresh = client.get('/api/schedule/sessions', headers=tokens['a'],
                       params={'date': '2030-03-11'}).json()
    assert fresh and all(s['capacity'] == 20 for s in fresh)


@pytest.mark.parametrize('payload', [
    {'capacity': 0}, {'capacity': 201}, {'duration_minutes': 10},
    {'duration_minutes': 301}, {'allowed_ptypes': 'VIP'}, {'ctype': 'OPEN'}, {'name': ' '},
])
def test_template_update_validates_fields(payload, client, world, tokens):
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.patch('/api/schedule/templates/' + template_id, headers=tokens['a'],
                        json=payload)
    assert resp.status_code == 400, resp.text
    assert resp.json()['error']['code'] == 'VALIDATION_ERROR'


def test_template_update_cross_tenant_404(client, world, tokens):
    template_id = world['factory'].template(world['tenant_b'], world['store_b'])
    resp = client.patch('/api/schedule/templates/' + template_id, headers=tokens['a'],
                        json={'name': '越权改名'})
    assert resp.status_code == 404, resp.text


# ==================== 六、角色可见性（T-27 后端面） ====================
def test_coach_reads_schedule_but_blocked_on_writes(client, world, tokens):
    """教练只看排课：GET 放行，所有写口 403（前端同时隐藏按钮）。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    coach = tokens['coach_a']

    assert client.get('/api/schedule/sessions', headers=coach).status_code == 200
    assert client.get('/api/schedule/templates', headers=coach).status_code == 200
    assert client.get('/api/tenant/stores', headers=coach).status_code == 200
    assert client.get('/api/tenant/coaches', headers=coach).status_code == 200

    forbidden = (
        client.post('/api/schedule/weekly', headers=coach,
                    json={'template_id': template_id, 'week_start': WEEK, 'times': ['09:00']}),
        client.post('/api/schedule/sessions', headers=coach,
                    json={'template_id': template_id, 'starts_at': '2030-03-05 09:00'}),
        client.patch('/api/schedule/templates/' + template_id, headers=coach,
                     json={'name': '越权改名'}),
        client.put('/api/tenant/stores/' + world['store_a'], headers=coach, json={'name': 'X'}),
        client.post('/api/tenant/stores/%s/disable' % world['store_a'], headers=coach),
        client.patch('/api/tenant/users/user-coach-a', headers=coach, json={'role': 'COACH'}),
        client.post('/api/tenant/users/user-coach-a/disable', headers=coach),
    )
    for resp in forbidden:
        assert resp.status_code == 403, resp.text
        assert resp.json()['error']['code'] == 'FORBIDDEN'


# ==================== 七、状态联动（QA 验收缺口 G1/G4/G5） ====================
def test_disabled_store_rejects_new_sessions(client, world, tokens):
    """停用门店不能再排课：单节与按周都要拦，否则会员端会继续放课。"""
    store_id = _make_store(client, tokens['a'], '停用即禁排门店', 'TS-G1')
    template_id = world['factory'].template(world['tenant_a'], store_id)
    assert client.post('/api/tenant/stores/%s/disable' % store_id,
                       headers=tokens['a']).status_code == 200

    single = client.post('/api/schedule/sessions', headers=tokens['a'], json={
        'template_id': template_id, 'starts_at': '2030-05-06 09:00'})
    assert single.status_code == 422, single.text
    assert '门店已停用' in single.json()['error']['message']

    weekly = _weekly(client, tokens['a'], template_id, times=['09:00'], week='2030-05-06')
    assert weekly.status_code == 422, weekly.text
    assert _count_sessions(world['tenant_a'], template_id) == 0, '停用门店不得留下课节'


def test_disabled_coach_dropped_from_dropdown_but_kept_for_names(client, world, tokens):
    """停用教练不再出现在字典默认结果里，但 include_disabled 仍可取到（供名称映射）。"""
    resp = client.post('/api/tenant/users', headers=tokens['a'], json={
        'username': 'coach_g4', 'password': '123456', 'real_name': '钱教练',
        'role': 'COACH', 'store_id': world['store_a']})
    user_id = resp.json()['id']
    assert client.post('/api/tenant/users/%s/disable' % user_id,
                       headers=tokens['a']).status_code == 200

    active = client.get('/api/tenant/coaches', headers=tokens['a']).json()
    assert user_id not in [c['id'] for c in active]

    full = client.get('/api/tenant/coaches', headers=tokens['a'],
                      params={'include_disabled': True}).json()
    row = next(c for c in full if c['id'] == user_id)
    assert row['status'] == 'DISABLED'
    assert row['real_name'] == '钱教练'


def test_weekly_skipped_counts_parsed_times(client, world, tokens):
    """skipped 必须按解析后的时段算：['9:00','09:00'] 是同一时刻，不能虚高成 14 个槽位。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    first = _weekly(client, tokens['a'], template_id, times=['9:00', '09:00'], week='2030-06-03')
    assert first.status_code == 200, first.text
    assert first.json()['created'] == 7
    assert first.json()['skipped'] == 0

    second = _weekly(client, tokens['a'], template_id, times=['9:00', '09:00'], week='2030-06-03')
    assert second.json()['created'] == 0
    assert second.json()['skipped'] == 7
    assert _count_sessions(world['tenant_a'], template_id) == 7


# ==================== 八、按周生成选「周几」（G2） ====================
def test_weekly_only_selected_weekdays(client, world, tokens):
    """days=[1,3,5] × 2 时段 → 6 节，且日期落在周一/三/五。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2030-09-02',   # 周一
        'times': ['09:00', '19:30'], 'days': [1, 3, 5]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['created'] == 6
    assert body['skipped'] == 0
    assert body['days'] == [1, 3, 5]

    rows = client.get('/api/schedule/sessions', headers=tokens['a'],
                      params={'date': '2030-09-02'}).json()
    assert [s['starts_at'] for s in rows] == ['2030-09-02 09:00', '2030-09-02 19:30']
    # 周二不该有课
    assert client.get('/api/schedule/sessions', headers=tokens['a'],
                      params={'date': '2030-09-03'}).json() == []
    # 周三有 2 节
    assert len(client.get('/api/schedule/sessions', headers=tokens['a'],
                          params={'date': '2030-09-04'}).json()) == 2

    repeat = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2030-09-02',
        'times': ['09:00', '19:30'], 'days': [1, 3, 5]})
    assert repeat.json()['created'] == 0
    assert repeat.json()['skipped'] == 6
    assert _count_sessions(world['tenant_a'], template_id) == 6


@pytest.mark.parametrize('days', [[], [0], [8], [0, 8], [1, 9], [True], [1.5], ['周一']])
def test_weekly_days_validation(days, client, world, tokens):
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2030-09-09',
        'times': ['09:00'], 'days': days})
    assert resp.status_code in (400, 422), resp.text
    assert resp.json()['error']['code'] in ('VALIDATION_ERROR', 'BUSINESS_RULE_VIOLATION')
    assert _count_sessions(world['tenant_a'], template_id) == 0


def test_normalize_days_rejects_bool_and_accepts_duplicates():
    """bool 不能当整数用（True == 1），重复值静默去重，None 回整周。"""
    from app.errors import ValidationError
    from app.services.schedule import normalize_days

    assert normalize_days(None) == [1, 2, 3, 4, 5, 6, 7]
    assert normalize_days([1, 1, 3]) == [1, 3]
    assert normalize_days([5, 2, 2]) == [2, 5]
    for bad in ([], [0], [8], [True], [1.5]):
        with pytest.raises(ValidationError):
            normalize_days(bad)


def test_weekly_days_defaults_to_whole_week(client, world, tokens):
    """不传 days 保持旧行为（整周），老客户端/脚本不会被打断。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = _weekly(client, tokens['a'], template_id, times=['09:00'], week='2030-09-16')
    assert resp.status_code == 200, resp.text
    assert resp.json()['created'] == 7
    assert resp.json()['days'] == [1, 2, 3, 4, 5, 6, 7]


def test_weekly_days_deduplicates_silently(client, world, tokens):
    """重复的星期按一次算，不报错也不重复建课。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2030-09-23',
        'times': ['09:00'], 'days': [2, 2, 4]})
    assert resp.status_code == 200, resp.text
    assert resp.json()['created'] == 2
    assert resp.json()['days'] == [2, 4]


def test_template_cannot_move_to_disabled_store(client, world, tokens):
    """把模板挪进已停用门店等于变相排课，必须 422；原地改名字不受影响。"""
    store_id = _make_store(client, tokens['a'], '模板挪店目标', 'TS-G1B')
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    assert client.post('/api/tenant/stores/%s/disable' % store_id,
                       headers=tokens['a']).status_code == 200

    resp = client.patch('/api/schedule/templates/' + template_id, headers=tokens['a'],
                        json={'store_id': store_id})
    assert resp.status_code == 422, resp.text
    assert '门店已停用' in resp.json()['error']['message']

    # 门店未变更时改名称放行
    rename = client.patch('/api/schedule/templates/' + template_id, headers=tokens['a'],
                          json={'name': '改名不受影响'})
    assert rename.status_code == 200, rename.text


# ==================== 九、周起始自动吸附周一（G6） ====================
def _session_starts(tenant_id: str, template_id: str):
    with SessionLocal() as db:
        rows = db.scalars(select(ClassSession).where(
            ClassSession.tenant_id == tenant_id,
            ClassSession.template_id == template_id)).all()
        return sorted(r.starts_at.strftime('%Y-%m-%d %H:%M') for r in rows)


def test_week_start_snaps_to_monday(client, world, tokens):
    """week_start 传周二（2026-03-03）也要落到该周的周一/三/五，不能整体错位。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2026-03-03',
        'times': ['09:00'], 'days': [1, 3, 5]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['created'] == 3
    assert body['week_start'] == '2026-03-02', '生效周起始必须是周一'
    assert body['requested_week_start'] == '2026-03-03'

    starts = _session_starts(world['tenant_a'], template_id)
    assert starts == ['2026-03-02 09:00', '2026-03-04 09:00', '2026-03-06 09:00']
    dates = {s[:10] for s in starts}
    assert dates.isdisjoint({'2026-03-03', '2026-03-05', '2026-03-07'}), \
        '不得落到入参当天/周四/周六（说明偏移仍基于原始日期）'


def test_week_start_sunday_falls_back_to_prev_monday(client, world, tokens):
    """周日属于「即将结束的那一周」，吸附后应回到上一个周一。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    resp = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2026-03-08',
        'times': ['09:00'], 'days': [1]})
    assert resp.status_code == 200, resp.text
    assert resp.json()['created'] == 1
    assert resp.json()['week_start'] == '2026-03-02'
    assert _session_starts(world['tenant_a'], template_id) == ['2026-03-02 09:00']


def test_week_start_snapping_keeps_idempotency(client, world, tokens):
    """同一周先用周二入参、再用周一入参，第二次必须 created=0（幂等键基于规范日期）。"""
    template_id = world['factory'].template(world['tenant_a'], world['store_a'])
    first = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2026-03-03',
        'times': ['09:00'], 'days': [1, 3, 5]})
    assert first.json()['created'] == 3

    second = client.post('/api/schedule/weekly', headers=tokens['a'], json={
        'template_id': template_id, 'week_start': '2026-03-02',
        'times': ['09:00'], 'days': [1, 3, 5]})
    assert second.status_code == 200, second.text
    assert second.json()['created'] == 0
    assert second.json()['skipped'] == 3
    assert _count_sessions(world['tenant_a'], template_id) == 3


def test_normalize_week_start_variants():
    from app.errors import ValidationError
    from app.services.schedule import normalize_week_start

    assert normalize_week_start('2026-03-02').strftime('%Y-%m-%d') == '2026-03-02'
    assert normalize_week_start('2026-03-03').strftime('%Y-%m-%d') == '2026-03-02'
    assert normalize_week_start('2026-03-08').strftime('%Y-%m-%d') == '2026-03-02'
    assert normalize_week_start('2026-03-09').strftime('%Y-%m-%d') == '2026-03-09'
    with pytest.raises(ValidationError):
        normalize_week_start('2026/03/02')
