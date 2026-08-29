"""约课服务：对应 PG 的 fn_lessons_next_two_weeks / fn_book / fn_unbook / fn_query_week_lessons。"""
import time

from .. import db
from ..timeutil import pg_dow

# 预约失败码
ERR_USER_BLOCKED = -100  # 学员被屏蔽
ERR_USER_NOT_FOUND = -200  # 学员未注册


def lessons_next_two_weeks(date_time: int, openid: str, class_type: int):
    """某日某类型课程列表。空结果返回 None（与 PG json_agg 空集 = null 一致）。"""
    rows = db.query_all(
        """
        SELECT course.id                                 AS course_id,
               course.peoples,
               (SELECT COUNT(reservation.id)
                FROM reservation
                WHERE reservation.course_id = course.id) AS `count`,
               (SELECT reservation.id
                FROM reservation
                         JOIN `user` u ON u.id = reservation.user_id
                WHERE u.open_id = %s
                  AND reservation.course_id = course.id
                LIMIT 1)                                 AS reservation_id,
               course.start_time,
               course.end_time,
               course.date_time,
               l.name                                    AS lesson_name,
               c.name                                    AS teacher_name,
               c.thumbnail,
               course.hidden
        FROM course
                 JOIN coach c ON course.teacher_id = c.id
                 JOIN lesson l ON l.id = course.lesson_id
        WHERE COALESCE(course.hidden, 0) <> 1
          AND course.date_time = %s
          AND course.class_type & %s = course.class_type
        """,
        (openid, date_time, class_type),
    )
    if not rows:
        return None
    return [
        {
            "course_id": r["course_id"],
            "peoples": r["peoples"],
            "count": int(r["count"] or 0),
            "reservation_id": r["reservation_id"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "date_time": r["date_time"],
            "lesson_name": r["lesson_name"],
            "teacher_name": r["teacher_name"],
            "thumbnail": r["thumbnail"],
            "hidden": r["hidden"],
        }
        for r in rows
    ]


def book(course_id: int, openid: str) -> int:
    """预约课程，返回预约 id；失败返回负数错误码。"""
    user = db.query_one(
        "SELECT id, COALESCE(user_type, 0) AS user_type FROM `user` WHERE open_id = %s LIMIT 1",
        (openid,),
    )
    if not user:
        return ERR_USER_NOT_FOUND
    if user["user_type"] == -1:
        return ERR_USER_BLOCKED

    course = db.query_one(
        "SELECT id FROM course WHERE id = %s LIMIT 1", (course_id,)
    )
    if not course:
        return -300  # 课程不存在（原版依赖外键报错，这里显式返回）

    now = int(time.time())
    return db.insert_return_id(
        """
        INSERT INTO reservation (course_id, fulfill, user_id, creation_time, updated_time, vc_id)
        VALUES (%s, 0, %s, %s, %s, 0)
        """,
        (course_id, user["id"], now, now),
    )


def unbook(reservation_id: int, openid: str) -> int:
    """取消预约（仅本人）。返回被删除的 id，0 = 失败。"""
    affected = db.execute(
        """
        DELETE FROM reservation
        WHERE id = %s
          AND user_id = (SELECT id FROM `user` WHERE open_id = %s LIMIT 1)
        """,
        (reservation_id, openid),
    )
    return reservation_id if affected > 0 else 0


def query_week_lessons():
    """本周（周一起 7 天）团课列表，按日期与开课时间排序。"""
    seconds = int(time.time())
    date_seconds = seconds - seconds % 86400 - 28800  # 北京时间当天 0 点
    week = pg_dow(seconds)
    if week == 0:  # 周日 → 从下周一起
        date_seconds += 86400
    else:
        date_seconds -= 86400 * (week - 1)
    date_times = [date_seconds + 86400 * i for i in range(7)]

    rows = db.query_all(
        """
        SELECT course.start_time,
               course.end_time,
               course.date_time,
               l.name AS lesson_name,
               c.name AS teacher_name
        FROM course
                 JOIN lesson l ON l.id = course.lesson_id
                 JOIN coach c ON course.teacher_id = c.id
        WHERE COALESCE(course.hidden, 0) = 0
          AND course.date_time IN ({placeholders})
          AND course.class_type = 4
        ORDER BY course.date_time, course.start_time
        """.format(placeholders=",".join(["%s"] * len(date_times))),
        tuple(date_times),
    )
    return [
        {
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "date_time": r["date_time"],
            "lesson_name": r["lesson_name"],
            "teacher_name": r["teacher_name"],
        }
        for r in rows
    ]
