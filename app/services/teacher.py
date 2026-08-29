"""教练主页：fn_teacher_lessons。"""
from .. import db


def teacher_lessons(start_time: int, end_time: int, openid: str, class_type: int, teacher_id: int):
    teacher = db.query_one(
        "SELECT name, thumbnail FROM coach WHERE id = %s", (teacher_id,)
    )
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
               l.name                                    AS lesson_name
        FROM course
                 JOIN lesson l ON l.id = course.lesson_id
        WHERE COALESCE(course.hidden, 0) = 0
          AND course.date_time >= %s
          AND course.date_time < %s
          AND course.class_type & %s = course.class_type
          AND course.teacher_id = %s
        """,
        (openid, start_time, end_time, class_type, teacher_id),
    )
    lessons = (
        [
            {
                "course_id": r["course_id"],
                "peoples": r["peoples"],
                "count": int(r["count"] or 0),
                "reservation_id": r["reservation_id"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "date_time": r["date_time"],
                "lesson_name": r["lesson_name"],
            }
            for r in rows
        ]
        or None
    )
    return {
        "teacher": (
            {"name": teacher["name"], "thumbnail": teacher["thumbnail"]}
            if teacher
            else None
        ),
        "lessons": lessons,
    }
