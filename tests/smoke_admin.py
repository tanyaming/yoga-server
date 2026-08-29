"""管理端接口自测：seed 数据 → 逐接口断言 → 清理。

运行：.venv/bin/python tests/smoke_admin.py
"""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from app import db  # noqa: E402
from app.timeutil import shanghai_midnight_ts  # noqa: E402

BASE = "http://127.0.0.1:8002"
ADMIN_OPENID = "oTEST_admin_" + "b" * 17  # 28 位
USER_OPENID = "oTEST_user_" + "a" * 17
TEACHER = "自测教练B"
LESSON = "自测流瑜伽"

today_ts = shanghai_midnight_ts(date.today())
next_week_ts = shanghai_midnight_ts(date.today() + timedelta(days=7))
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  --> {detail}"))


def cleanup():
    db.execute(
        "DELETE FROM course WHERE lesson_id IN (SELECT id FROM lesson WHERE name=%s)", (LESSON,))
    db.execute("DELETE FROM lesson WHERE name=%s", (LESSON,))
    db.execute("DELETE FROM coach WHERE name=%s", (TEACHER,))
    db.execute("DELETE FROM `user` WHERE open_id IN (%s, %s)", (ADMIN_OPENID, USER_OPENID))


def seed():
    cleanup()
    teacher_id = db.insert_return_id("INSERT INTO coach (name, thumbnail, introduction) VALUES (%s, %s, %s)", (TEACHER, "t.jpg", "简介"))
    lesson_id = db.insert_return_id("INSERT INTO lesson (name) VALUES (%s)", (LESSON,))
    now = int(time.time())
    # 本周 + 下周同星期两节课（同 start_time 同 class_type，构成周系列）
    c1 = db.insert_return_id(
        "INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples, start_time, teacher_id, creation_time, updated_time) VALUES (4,%s,%s,41400,10,36000,%s,%s,%s)",
        (lesson_id, today_ts, teacher_id, now, now))
    c2 = db.insert_return_id(
        "INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples, start_time, teacher_id, creation_time, updated_time) VALUES (4,%s,%s,41400,10,36000,%s,%s,%s)",
        (lesson_id, next_week_ts, teacher_id, now, now))
    admin_id = db.insert_return_id(
        "INSERT INTO `user` (open_id, nick_name, user_type, creation_time, updated_time) VALUES (%s, '自测管理员', 4, %s, %s)",
        (ADMIN_OPENID, now, now))
    user_id = db.insert_return_id(
        "INSERT INTO `user` (open_id, nick_name, user_type, phone, creation_time, updated_time) VALUES (%s, '自测会员', 0, '13811112222', %s, %s)",
        (USER_OPENID, now, now))
    return teacher_id, lesson_id, c1, c2, admin_id, user_id


def main():
    teacher_id, lesson_id, c1, c2, admin_id, user_id = seed()

    # 1. 课程列表（含本周）
    r = requests.get(f"{BASE}/yoga/admin/lessons", params={"start": today_ts, "end": today_ts + 86400})
    ids = {x["course_id"] for x in (r.json() or [])}
    check("admin/lessons 列表含今日课程", c1 in ids, r.text)

    # 2. 单课详情
    r = requests.get(f"{BASE}/yoga/admin/lesson", params={"id": c1})
    j = r.json()
    check("admin/lesson 详情", j["course_id"] == c1 and j["lesson_name"] == LESSON and j["teacher_name"] == TEACHER, r.text)

    # 3. 非管理员查会员列表 → null
    r = requests.get(f"{BASE}/yoga/admin/users/all", params={"open_id": USER_OPENID})
    check("admin/users/all 非管理员返回 null", r.json() is None, r.text)

    # 4. 管理员查会员列表
    r = requests.get(f"{BASE}/yoga/admin/users/all", params={"open_id": ADMIN_OPENID})
    j = r.json()
    check("admin/users/all 管理员可见且不含管理员自身", j and any(x["id"] == user_id for x in j)
          and not any(x["id"] == admin_id for x in j), r.text)

    # 5. 会员详情
    r = requests.get(f"{BASE}/yoga/admin/user", params={"open_id": ADMIN_OPENID, "id": user_id})
    j = r.json()
    check("admin/user 会员详情", j and j["phone"] == "13811112222" and j["nick_name"] == "自测会员", r.text)

    # 6. 停课
    r = requests.get(f"{BASE}/yoga/admin/lesson/hidden", params={"id": c1, "status": 1})
    check("admin/lesson/hidden 停课", int(r.text) == c1, r.text)
    r = requests.get(f"{BASE}/yoga/lessons", params={"start": today_ts, "openid": USER_OPENID, "class_type": 4})
    ids = {x["course_id"] for x in (r.json() or [])}
    check("停课后用户端不可见", c1 not in ids, r.text)
    r = requests.get(f"{BASE}/yoga/admin/lessons", params={"start": today_ts, "end": today_ts + 86400})
    j = next((x for x in r.json() if x["course_id"] == c1), None)
    check("停课后管理端仍可见且 hidden=1", j and j["hidden"] == 1, r.text)
    requests.get(f"{BASE}/yoga/admin/lesson/hidden", params={"id": c1, "status": 0})

    # 7. 排课编辑数据
    r = requests.get(f"{BASE}/yoga/admin/lessons/and/teachers", params={"id": c1})
    j = r.json()
    check("admin/lessons/and/teachers", LESSON in j["lessons"] and TEACHER in j["teachers"]
          and j["lesson"]["lesson_name"] == LESSON and j["lesson"]["peoples"] == 10, r.text)

    # 8. 会员先约一节课，然后查约课记录
    rid = int(requests.get(f"{BASE}/yoga/book", params={"id": c1, "openid": USER_OPENID}).text)
    r = requests.get(f"{BASE}/yoga/admin/user/lessons", params={"id": user_id, "start": 0, "end": 0, "open_id": ADMIN_OPENID})
    j = r.json()
    check("admin/user/lessons 约课记录", j and j[0]["id"] == rid and j[0]["lesson_name"] == LESSON, r.text)
    r = requests.get(f"{BASE}/yoga/admin/user/lessons", params={"id": user_id, "start": today_ts + 86400, "end": today_ts + 2 * 86400, "open_id": ADMIN_OPENID})
    check("admin/user/lessons 时间过滤后为空", r.json() is None, r.text)

    # 9. 删除课程（连带预约）
    r = requests.get(f"{BASE}/yoga/admin/lesson/delete", params={"id": c1})
    check("admin/lesson/delete 删除", int(r.text) == c1, r.text)
    left = db.query_one("SELECT COUNT(*) AS c FROM reservation WHERE course_id=%s", (c1,))["c"]
    check("删除课程连带清理预约", left == 0, str(left))
    r = requests.get(f"{BASE}/yoga/admin/lesson/delete", params={"id": c1})
    check("重复删除返回 0", r.text == "0", r.text)

    # 10. 按周系列批量修改（改 c2 所在系列容量）
    r = requests.post(f"{BASE}/yoga/lesson/update", json={
        "id": c2, "lesson": LESSON, "teacher": TEACHER, "class_type": 4,
        "start_time": 36000, "end_time": 46800, "peoples": 20,
        "old_start_time": 36000, "old_class_type": 4})
    check("lesson/update 返回 0", r.text == "0", r.text)
    row = db.query_one("SELECT peoples, end_time FROM course WHERE id=%s", (c2,))
    check("lesson/update 系列课被修改", row["peoples"] == 20 and row["end_time"] == 46800, str(row))

    # 11. 按星期批量排课
    r = requests.post(f"{BASE}/yoga/admin/lessons/update", params={"open_id": ADMIN_OPENID}, json={
        "class_type": 4, "start_time": 39600, "end_time": 45000, "peoples": 8,
        "date_time": 1, "lesson": LESSON, "teacher": TEACHER})
    created = int(r.text)
    cnt = db.query_one("SELECT COUNT(*) AS c FROM course WHERE lesson_id=%s AND start_time=39600", (lesson_id,))["c"]
    check("admin/lessons/update 批量排课约 52 节", created == cnt and 50 <= cnt <= 54, f"ret={created} rows={cnt}")
    dows = db.query_all("SELECT DISTINCT date_time AS d FROM course WHERE lesson_id=%s AND start_time=39600", (lesson_id,))
    import datetime as dt
    ok_dow = all(dt.datetime.utcfromtimestamp(x["d"] + 28800).weekday() == 0 for x in dows)
    check("批量排课全部落在周一", ok_dow, str(dows[:2]))

    # 12. 教练不存在 → -1
    r = requests.post(f"{BASE}/yoga/admin/lessons/update", json={
        "class_type": 4, "start_time": 39600, "end_time": 45000, "peoples": 8,
        "date_time": 1, "lesson": LESSON, "teacher": "不存在的教练"})
    check("admin/lessons/update 教练不存在返回 -1", r.text == "-1", r.text)

    cleanup()
    passed = sum(1 for _, c, _ in results if c)
    print(f"\n===== 管理端自测：{passed}/{len(results)} 通过 =====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
