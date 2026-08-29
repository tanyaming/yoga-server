"""用户端接口自测：seed 数据 → 逐接口断言 → 清理。

运行：.venv/bin/python tests/smoke_user.py
"""
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from app import db  # noqa: E402
from app.timeutil import shanghai_midnight_ts  # noqa: E402

BASE = "http://127.0.0.1:8002"
OPENID = "oTEST_user_" + "a" * 17  # 28 位符合 debug 校验的 openid
TEACHER = "自测教练A"
LESSON = "自测哈他瑜伽"

today_ts = shanghai_midnight_ts(date.today())
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  --> {detail}"))


def seed():
    db.execute("DELETE FROM reservation WHERE course_id IN (SELECT id FROM course WHERE lesson_id IN (SELECT id FROM lesson WHERE name=%s))", (LESSON,))
    db.execute("DELETE FROM course WHERE lesson_id IN (SELECT id FROM lesson WHERE name=%s)", (LESSON,))
    db.execute("DELETE FROM lesson WHERE name=%s", (LESSON,))
    db.execute("DELETE FROM coach WHERE name=%s", (TEACHER,))
    db.execute("DELETE FROM `user` WHERE open_id=%s", (OPENID,))
    db.execute("DELETE FROM systeminfo WHERE open_id=%s", (OPENID,))
    db.execute("DELETE FROM announcement WHERE title LIKE %s", ("自测公告%",))
    db.execute("DELETE FROM market WHERE slogan LIKE %s", ("自测%",))

    teacher_id = db.insert_return_id("INSERT INTO coach (name, thumbnail, introduction) VALUES (%s, %s, %s)", (TEACHER, "t.jpg", "简介"))
    lesson_id = db.insert_return_id("INSERT INTO lesson (name) VALUES (%s)", (LESSON,))
    now = int(time.time())
    course_id = db.insert_return_id(
        "INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples, start_time, teacher_id, creation_time, updated_time) VALUES (4,%s,%s,41400,10,36000,%s,%s,%s)",
        (lesson_id, today_ts, teacher_id, now, now),
    )
    course_9am = db.insert_return_id(
        "INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples, start_time, teacher_id, creation_time, updated_time) VALUES (4,%s,%s,41400,10,32400,%s,%s,%s)",
        (lesson_id, today_ts, teacher_id, now, now),
    )
    db.execute("DELETE FROM slideshow")
    db.insert_return_id("INSERT INTO slideshow (image) VALUES ('s1.jpg')")
    db.execute("DELETE FROM `function`")
    db.insert_return_id("INSERT INTO `function` (id, name, image) VALUES (2, '约课', 'f.jpg')")
    db.insert_return_id("INSERT INTO announcement (title, content, updated_time) VALUES (%s, %s, %s)", ("自测公告1", "内容", now))
    db.insert_return_id("INSERT INTO market (title, slogan, content, updated_time) VALUES (%s, %s, %s, %s)", ("自测会员卡", "自测办卡优惠", "内容", now))
    return teacher_id, course_id, course_9am


def cleanup():
    db.execute("DELETE FROM reservation WHERE course_id IN (SELECT id FROM course WHERE lesson_id IN (SELECT id FROM lesson WHERE name=%s))", (LESSON,))
    db.execute("DELETE FROM course WHERE lesson_id IN (SELECT id FROM lesson WHERE name=%s)", (LESSON,))
    db.execute("DELETE FROM lesson WHERE name=%s", (LESSON,))
    db.execute("DELETE FROM coach WHERE name=%s", (TEACHER,))
    db.execute("DELETE FROM `user` WHERE open_id=%s", (OPENID,))
    db.execute("DELETE FROM systeminfo WHERE open_id=%s", (OPENID,))
    db.execute("DELETE FROM announcement WHERE title LIKE %s", ("自测公告%",))
    db.execute("DELETE FROM market WHERE slogan LIKE %s", ("自测%",))


def main():
    teacher_id, course_id, course_9am = seed()

    # 1. 未注册时查询 → null
    r = requests.get(f"{BASE}/yoga/user/query", params={"openid": OPENID})
    check("user/query 未注册返回 null", r.status_code == 200 and r.json() is None, r.text)

    # 2. 注册
    r = requests.post(f"{BASE}/yoga/user", json={
        "open_id": OPENID, "nick_name": "自测学员", "avatar_url": "a.jpg",
        "phone": "13800000000", "gender": 1, "user_type": 0,
    })
    user_id = int(r.text)
    check("POST /yoga/user 注册返回 id", user_id > 0, r.text)

    # 3. 查询资料
    r = requests.get(f"{BASE}/yoga/user/query", params={"openid": OPENID})
    j = r.json()
    check("user/query 注册后返回资料", j and j["id"] == user_id and j["nick_name"] == "自测学员", r.text)

    # 4. 再更新（nick_name 缺省应保留）
    requests.post(f"{BASE}/yoga/user", json={"open_id": OPENID, "phone": "13900000000"})
    r = requests.get(f"{BASE}/yoga/user/query", params={"openid": OPENID})
    j = r.json()
    check("POST /yoga/user 更新不影响昵称", j["nick_name"] == "自测学员", r.text)

    # 5. 首页聚合
    r = requests.get(f"{BASE}/yoga/index")
    j = r.json()
    check("index 结构完整",
          all(k in j for k in ("poster", "booked", "actions", "teachers", "market", "notices"))
          and j["poster"][0]["image"] == "s1.jpg"
          and j["actions"][0]["name"] == "约课"
          and j["market"]["slogan"] == "自测办卡优惠"
          and j["notices"][0]["title"] == "自测公告1"
          and any(t["name"] == TEACHER for t in (j["teachers"] or [])), r.text)

    # 6. 课程列表（未约）
    r = requests.get(f"{BASE}/yoga/lessons", params={"start": today_ts, "openid": OPENID, "class_type": 4})
    j = r.json()
    target = next((x for x in j if x["course_id"] == course_id), None)
    check("lessons 列表含今日课程且未预约", target and target["reservation_id"] is None and target["count"] == 0
          and target["teacher_name"] == TEACHER and target["lesson_name"] == LESSON, r.text)

    # 7. 预约
    r = requests.get(f"{BASE}/yoga/book", params={"id": course_id, "openid": OPENID})
    rid = int(r.text)
    check("book 预约成功", rid > 0, r.text)

    # 8. 预约后列表
    r = requests.get(f"{BASE}/yoga/lessons", params={"start": today_ts, "openid": OPENID, "class_type": 4})
    target = next((x for x in r.json() if x["course_id"] == course_id), None)
    check("lessons 预约后 reservation_id/count 正确", target["reservation_id"] == rid and target["count"] == 1, r.text)

    # 9. 预约不存在的课程 → -300
    r = requests.get(f"{BASE}/yoga/book", params={"id": 999999, "openid": OPENID})
    check("book 不存在课程返回 -300", r.text == "-300", r.text)

    # 10. 取消
    r = requests.get(f"{BASE}/yoga/unbook", params={"id": rid, "openid": OPENID})
    check("unbook 取消成功", int(r.text) == rid, r.text)
    r = requests.get(f"{BASE}/yoga/unbook", params={"id": rid, "openid": OPENID})
    check("unbook 重复取消返回 0", r.text == "0", r.text)

    # 11. 统计
    requests.get(f"{BASE}/yoga/book", params={"id": course_id, "openid": OPENID})
    r = requests.get(f"{BASE}/yoga/user/book/statistics", params={"id": OPENID})
    j = r.json()
    check("user/book/statistics 统计正确", j and j["big"] == 1 and j["one"] == 0 and j["small"] == 0, r.text)

    # 12. 教练主页
    r = requests.get(f"{BASE}/yoga/teacher/lessons", params={
        "start_time": today_ts, "end_time": today_ts + 86400, "open_id": OPENID,
        "class_type": 4, "teacher_id": teacher_id})
    j = r.json()
    course_ids = {x["course_id"] for x in j["lessons"]}
    check("teacher/lessons 教练与课程", j["teacher"]["name"] == TEACHER and course_ids == {course_id, course_9am}, r.text)

    # 13. auth（secret 未配置，微信会返回错误 JSON）
    r = requests.get(f"{BASE}/yoga/auth", params={"code": "fake_code"})
    check("auth 代理返回微信 JSON", r.status_code == 200 and "errcode" in r.json(), r.text)

    # 14. debug 合法数据
    r = requests.post(f"{BASE}/yoga/debug", json={
        "open_id": OPENID, "sdk_version": "3.3.5", "screen_width": "375",
        "pixel_ratio": "2.0", "brand": "devtools", "model": "iPhone 15",
        "platform": "devtools", "version": "1.0.0", "screen_height": "812"})
    dbg_id = int(r.text)
    check("debug 合法数据入库", dbg_id > 0, r.text)
    # 再报一次 → count 递增
    requests.post(f"{BASE}/yoga/debug", json={
        "open_id": OPENID, "sdk_version": "3.3.5", "screen_width": "375",
        "pixel_ratio": "2.0"})
    cnt = db.query_one("SELECT `count` AS c FROM systeminfo WHERE open_id=%s", (OPENID,))["c"]
    check("debug 重复上报 count 递增", cnt == 2, str(cnt))
    # 非法 openid → -1
    r = requests.post(f"{BASE}/yoga/debug", json={
        "open_id": "short", "sdk_version": "3.3.5", "screen_width": "375", "pixel_ratio": "2"})
    check("debug 非法数据返回 -1", r.text == "-1", r.text)

    # 15. 周课表图片
    r = requests.get(f"{BASE}/yoga/admin/schedule")
    check("admin/schedule 返回 PNG", r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n", f"{r.status_code} len={len(r.content)}")

    # 16. 头像上传
    r = requests.post(f"{BASE}/yoga/avatar", files={"file": ("avatar123", b"\x89PNG fakedata")})
    check("avatar 上传返回文件名", r.status_code == 200 and r.text == "avatar123.avif", r.text)

    cleanup()
    passed = sum(1 for _, c, _ in results if c)
    print(f"\n===== 用户端自测：{passed}/{len(results)} 通过 =====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
