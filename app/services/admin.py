"""管理端服务：对应 fn_admin_* 系列函数。"""
import time
from datetime import date, timedelta

from .. import db
from ..timeutil import now_ts, shanghai_midnight_ts, today_sh


def _parse_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def admin_lessons(start: int, end: int):
    """管理端课程列表（团课+小班，含已停课）。"""
    rows = db.query_all(
        """
        SELECT course.id                                 AS course_id,
               course.peoples,
               (SELECT COUNT(reservation.id)
                FROM reservation
                WHERE reservation.course_id = course.id) AS `count`,
               course.start_time,
               course.end_time,
               course.date_time,
               l.name                                    AS lesson_name,
               c.name                                    AS teacher_name,
               c.thumbnail,
               course.hidden,
               course.class_type
        FROM course
                 JOIN coach c ON course.teacher_id = c.id
                 JOIN lesson l ON l.id = course.lesson_id
        WHERE course.date_time >= %s
          AND course.date_time < %s
          AND course.class_type & 5 = course.class_type
        ORDER BY course.date_time, course.start_time
        """,
        (start, end),
    )
    if not rows:
        return None
    return [
        {
            "course_id": r["course_id"],
            "peoples": r["peoples"],
            "count": int(r["count"] or 0),
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "date_time": r["date_time"],
            "lesson_name": r["lesson_name"],
            "teacher_name": r["teacher_name"],
            "thumbnail": r["thumbnail"],
            "hidden": r["hidden"],
            "class_type": r["class_type"],
        }
        for r in rows
    ]


def admin_lesson(course_id: int):
    """单节课详情（原仓库未公开 fn_admin_lesson，按同字段结构实现）。"""
    r = db.query_one(
        """
        SELECT course.id                                 AS course_id,
               course.peoples,
               (SELECT COUNT(reservation.id)
                FROM reservation
                WHERE reservation.course_id = course.id) AS `count`,
               course.start_time,
               course.end_time,
               course.date_time,
               l.name                                    AS lesson_name,
               c.name                                    AS teacher_name,
               c.thumbnail,
               course.hidden,
               course.class_type
        FROM course
                 JOIN coach c ON course.teacher_id = c.id
                 JOIN lesson l ON l.id = course.lesson_id
        WHERE course.id = %s
        """,
        (course_id,),
    )
    if not r:
        return None
    return {
        "course_id": r["course_id"],
        "peoples": r["peoples"],
        "count": int(r["count"] or 0),
        "start_time": r["start_time"],
        "end_time": r["end_time"],
        "date_time": r["date_time"],
        "lesson_name": r["lesson_name"],
        "teacher_name": r["teacher_name"],
        "thumbnail": r["thumbnail"],
        "hidden": r["hidden"],
        "class_type": r["class_type"],
    }


def admin_lesson_update_status(course_id: int, value: int) -> int:
    """停课/恢复（hidden），返回课程 id，0 = 失败。"""
    affected = db.execute(
        "UPDATE course SET hidden = %s WHERE id = %s", (value, course_id)
    )
    return course_id if affected > 0 else 0


def admin_lesson_delete(course_id: int) -> int:
    """删除课程（连带清理其预约记录），返回课程 id，0 = 失败。"""
    affected = db.execute("DELETE FROM course WHERE id = %s", (course_id,))
    if affected == 0:
        return 0
    db.execute("DELETE FROM reservation WHERE course_id = %s", (course_id,))
    return course_id


def admin_lessons_and_teachers(course_id: int):
    """排课编辑页数据：全部课名/教练名 + 当前课程详情。"""
    lessons = [r["name"] for r in db.query_all("SELECT name FROM lesson")]
    teachers = [r["name"] for r in db.query_all("SELECT name FROM coach")]
    r = db.query_one(
        """
        SELECT l.peoples, l.start_time, l.class_type, l2.name AS lesson_name, c.name AS teacher_name
        FROM course l
                 JOIN lesson l2 ON l2.id = l.lesson_id
                 JOIN coach c ON c.id = l.teacher_id
        WHERE l.id = %s
        """,
        (course_id,),
    )
    lesson = (
        {
            "peoples": r["peoples"],
            "start_time": r["start_time"],
            "class_type": r["class_type"],
            "lesson_name": r["lesson_name"],
            "teacher_name": r["teacher_name"],
        }
        if r
        else None
    )
    return {"lessons": lessons, "teachers": teachers, "lesson": lesson}


def admin_lesson_update(obj: dict) -> int:
    """按周系列批量修改课程（从指定课程所在日期起、同星期、同时段同类型的课全部更新）。"""
    course_id = _parse_int(obj.get("id"), 0)
    if course_id == 0:
        return 0
    target = db.query_one(
        "SELECT date_time FROM course WHERE id = %s LIMIT 1", (course_id,)
    )
    if not target:
        return 0
    dt = target["date_time"]

    old_start_time = _parse_int(obj.get("old_start_time"), 0)
    old_class_type = _parse_int(obj.get("old_class_type"), 0)

    lesson_id = (db.query_one("SELECT id AS i FROM lesson WHERE name = %s LIMIT 1", (obj.get("lesson"),)) or {}).get("i", 0) or 0
    teacher_id = (db.query_one("SELECT id AS i FROM coach WHERE name = %s LIMIT 1", (obj.get("teacher"),)) or {}).get("i", 0) or 0

    v_class_type = _parse_int(obj.get("class_type"), 0) or None
    v_start_time = _parse_int(obj.get("start_time"), 0) or None
    v_end_time = _parse_int(obj.get("end_time"), 0) or None
    v_peoples = _parse_int(obj.get("peoples"), 0) or None

    ids = [
        r["id"]
        for r in db.query_all(
            """
            SELECT c.id
            FROM course c
            WHERE c.date_time >= %s
              AND (%s - c.date_time) %% 604800 = 0
              AND c.start_time = %s
              AND c.class_type = %s
            """,
            (dt, dt, old_start_time, old_class_type),
        )
    ]
    for cid in ids:
        sets, params = [], []
        if lesson_id:
            sets.append("lesson_id = %s")
            params.append(lesson_id)
        if teacher_id:
            sets.append("teacher_id = %s")
            params.append(teacher_id)
        if v_class_type:
            sets.append("class_type = %s")
            params.append(v_class_type)
        if v_start_time:
            sets.append("start_time = %s")
            params.append(v_start_time)
        if v_end_time:
            sets.append("end_time = %s")
            params.append(v_end_time)
        if v_peoples:
            sets.append("peoples = %s")
            params.append(v_peoples)
        if sets:
            params.append(now_ts())
            sets.append("updated_time = %s")
            params.append(cid)
            db.execute(f"UPDATE course SET {', '.join(sets)} WHERE id = %s", tuple(params))
    return 0


def admin_lessons_update(obj: dict) -> int:
    """按星期批量排课：从今天起一年内，每周指定星期各生成一节课。返回生成条数，-1 = 课/教练不存在。"""
    v_class_type = _parse_int(obj.get("class_type"), 0) or 4
    v_start_time = _parse_int(obj.get("start_time"), 0)
    v_end_time = _parse_int(obj.get("end_time"), 0)
    v_peoples = _parse_int(obj.get("peoples"), 0)
    v_week = _parse_int(obj.get("date_time"), 0)  # 0=周日 1=周一 ... 6=周六（PG dow）

    lesson_id = (db.query_one("SELECT id AS i FROM lesson WHERE name = %s LIMIT 1", (obj.get("lesson"),)) or {}).get("i", 0)
    if not lesson_id:
        return -1
    teacher_id = (db.query_one("SELECT id AS i FROM coach WHERE name = %s LIMIT 1", (obj.get("teacher"),)) or {}).get("i", 0)
    if not teacher_id:
        return -1

    now = now_ts()
    today = today_sh()
    # PG dow(周日=0) → Python weekday(周一=0)
    target_weekday = (v_week - 1) % 7
    offset = (target_weekday - today.weekday()) % 7
    d = today + timedelta(days=offset)
    created = 0
    while d <= today + timedelta(days=365):
        date_time = shanghai_midnight_ts(d)
        db.execute(
            """
            INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples,
                                start_time, teacher_id, creation_time, updated_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (v_class_type, lesson_id, date_time, v_end_time, v_peoples,
             v_start_time, teacher_id, now, now),
        )
        created += 1
        d += timedelta(days=7)
    return created


def admin_user_lessons(user_id: int, start: int, end: int):
    """某学员的约课记录（start=0 时查全部）。"""
    rows = db.query_all(
        """
        SELECT r.id, c.date_time, c.class_type, c.start_time, c.end_time,
               l.name AS lesson_name, c2.name AS teacher_name, c2.thumbnail
        FROM reservation r
                 JOIN `user` u ON u.id = r.user_id
                 JOIN course c ON c.id = r.course_id
                 JOIN lesson l ON l.id = c.lesson_id
                 JOIN coach c2 ON c.teacher_id = c2.id
        WHERE u.id = %s
          AND (%s = 0 OR (c.date_time >= %s AND c.date_time < %s))
        ORDER BY c.date_time DESC
        """,
        (user_id, start, start, end),
    )
    if not rows:
        return None
    return [
        {
            "id": r["id"],
            "date_time": r["date_time"],
            "class_type": r["class_type"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "lesson_name": r["lesson_name"],
            "teacher_name": r["teacher_name"],
            "thumbnail": r["thumbnail"],
        }
        for r in rows
    ]


def admin_users_all(open_id: str):
    """全部会员列表（仅管理员 user_type=4 可见）。"""
    admin = db.query_one(
        "SELECT user_type FROM `user` WHERE open_id = %s LIMIT 1", (open_id,)
    )
    if not admin or admin["user_type"] != 4:
        return None
    rows = db.query_all(
        """
        SELECT u.id, u.nick_name, u.avatar_url, u.creation_time
        FROM `user` u
        WHERE u.user_type IS NULL OR (u.user_type <> -1 AND u.user_type <> 4)
        """
    )
    return [
        {
            "id": r["id"],
            "nick_name": r["nick_name"],
            "avatar_url": r["avatar_url"],
            "creation_time": r["creation_time"],
        }
        for r in rows
    ]


def admin_user(user_id: int):
    """会员详情（原仓库未公开 fn_admin_user，按完整用户资料实现）。"""
    r = db.query_one(
        """
        SELECT id, open_id, name, nick_name, avatar_url, phone, gender, address,
               note, user_type, creation_time, updated_time
        FROM `user` WHERE id = %s LIMIT 1
        """,
        (user_id,),
    )
    if not r:
        return None
    return {
        "id": r["id"],
        "open_id": r["open_id"],
        "name": r["name"],
        "nick_name": r["nick_name"],
        "avatar_url": r["avatar_url"],
        "phone": r["phone"],
        "gender": r["gender"],
        "address": r["address"],
        "note": r["note"],
        "user_type": r["user_type"],
        "creation_time": r["creation_time"],
        "updated_time": r["updated_time"],
    }
