"""管理后台演示数据种子脚本。

灌入：1 个管理员 + 3 教练 + 4 课程 + 本周排课 + 5 会员 + 部分预约 + 公告/商城。
幂等：重复执行不会重复插入（按固定 open_id / 名称先清理）。
"""
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, ".")
from app import db  # noqa: E402
from app.timeutil import now_ts, shanghai_midnight_ts  # noqa: E402

ADMIN_OID = "admin_local_000000000000000000"
COACHES = [("林清雅", "全美瑜伽联盟 RYT-500 认证，专注哈他与阴瑜伽教学 8 年。"),
           ("王梓涵", "流瑜伽与空中瑜伽资深导师，擅长体式精进。"),
           ("陈默", "阿斯汤伽习练者，私教课程广受好评。")]
LESSONS = ["哈他瑜伽", "阴瑜伽", "流瑜伽", "空中瑜伽"]
MEMBERS = [
    ("oTEST_member_0000000000000001", "小鹿", 2), ("oTEST_member_0000000000000002", "苏苏", 2),
    ("oTEST_member_0000000000000003", "阿May", 2), ("oTEST_member_0000000000000004", "石头", 1),
    ("oTEST_member_0000000000000005", "南音", 2),
]

now = now_ts()

# ---- 清理旧演示数据（幂等） ----
db.execute("DELETE FROM reservation WHERE user_id IN (SELECT id FROM `user` WHERE open_id LIKE %s OR open_id=%s)",
           ("oTEST_member_%", ADMIN_OID))
db.execute("DELETE FROM course WHERE lesson_id IN (SELECT id FROM lesson WHERE name IN %s)", (LESSONS,))
db.execute("DELETE FROM lesson WHERE name IN %s", (LESSONS,))
db.execute("DELETE FROM coach WHERE name IN %s", (tuple(c[0] for c in COACHES),))
db.execute("DELETE FROM `user` WHERE open_id LIKE %s OR open_id=%s", ("oTEST_member_%", ADMIN_OID))
db.execute("DELETE FROM announcement WHERE title LIKE %s", ("演示%",))
db.execute("DELETE FROM market WHERE slogan LIKE %s", ("演示%",))

# ---- 管理员 ----
admin_id = db.insert_return_id(
    "INSERT INTO `user` (open_id, nick_name, user_type, creation_time, updated_time) VALUES (%s, %s, 4, %s, %s)",
    (ADMIN_OID, "管理员", now, now))

# ---- 教练 / 课程库 ----
coach_ids = {}
for name, intro in COACHES:
    coach_ids[name] = db.insert_return_id(
        "INSERT INTO coach (name, thumbnail, introduction) VALUES (%s, '', %s)", (name, intro))
lesson_ids = {}
for name in LESSONS:
    lesson_ids[name] = db.insert_return_id("INSERT INTO lesson (name) VALUES (%s)", (name,))

# ---- 本周排课：周一~周日 每天 9:00 哈他 / 19:00 阴瑜伽（周六 19:00 流瑜伽）----
monday = date.today() - timedelta(days=date.today().weekday())
course_ids = []
for i in range(7):
    d = monday + timedelta(days=i)
    ts = shanghai_midnight_ts(d)
    m1 = "哈他瑜伽" if i < 5 else "空中瑜伽"
    t1 = "林清雅" if i % 2 == 0 else "王梓涵"
    cid = db.insert_return_id(
        "INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples, start_time, teacher_id, creation_time, updated_time)"
        " VALUES (4, %s, %s, 41400, 10, 32400, %s, %s, %s)",
        (lesson_ids[m1], ts, coach_ids[t1], now, now))
    course_ids.append(cid)
    m2 = "流瑜伽" if i == 5 else "阴瑜伽"
    cid2 = db.insert_return_id(
        "INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples, start_time, teacher_id, creation_time, updated_time)"
        " VALUES (4, %s, %s, 68400, 8, 64800, %s, %s, %s)",
        (lesson_ids[m2], ts, coach_ids["陈默" if i % 3 == 0 else "林清雅"], now, now))
    course_ids.append(cid2)

# ---- 会员 ----
member_ids = []
for oid, nick, gender in MEMBERS:
    uid = db.insert_return_id(
        "INSERT INTO `user` (open_id, nick_name, gender, user_type, phone, creation_time, updated_time)"
        " VALUES (%s, %s, %s, 0, %s, %s, %s)",
        (oid, nick, gender, f"138{abs(hash(oid)) % 10**8:08d}", now, now))
    member_ids.append(uid)

# ---- 预约：前 4 名会员随机约前 8 节课 ----
for uid, cid in zip(member_ids[:4], course_ids[:8]):
    db.execute(
        "INSERT INTO reservation (course_id, user_id, creation_time, updated_time) VALUES (%s, %s, %s, %s)",
        (cid, uid, now, now))

# ---- 公告 / 商城 ----
db.execute("INSERT INTO announcement (title, updated_time) VALUES (%s, %s)", ("演示公告：国庆假期课表调整", now))
db.execute("INSERT INTO announcement (title, updated_time) VALUES (%s, %s)", ("演示公告：新教练陈默加入", now - 86400))
db.execute("INSERT INTO market (slogan, updated_time) VALUES (%s, %s)", ("演示文案：金秋办卡享 8 折", now))

print(f"seeded: admin#{admin_id}, {len(coach_ids)} coaches, {len(lesson_ids)} lessons, "
      f"{len(course_ids)} courses, {len(member_ids)} members, "
      f"{min(4, len(member_ids))} reservations")
