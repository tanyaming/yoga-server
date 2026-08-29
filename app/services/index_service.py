"""首页聚合：fn_index。"""
from .. import db


def _agg(rows, keys):
    """json_agg：空集返回 None。"""
    if not rows:
        return None
    return [{k: r[k] for k in keys} for r in rows]


def index():
    poster = _agg(db.query_all("SELECT id, image FROM slideshow"), ("id", "image"))

    booked = _agg(
        db.query_all(
            """
            SELECT reservation.id AS id, u.nick_name AS nick_name, u.avatar_url AS avatar_url
            FROM reservation
                     JOIN `user` u ON u.id = reservation.user_id
            WHERE reservation.id IN (SELECT MAX(reservation.id)
                                     FROM reservation
                                     GROUP BY reservation.user_id)
            ORDER BY reservation.creation_time DESC
            LIMIT 10
            """
        ),
        ("id", "nick_name", "avatar_url"),
    )

    actions = _agg(
        db.query_all("SELECT id, name, image FROM `function` ORDER BY id"),
        ("id", "name", "image"),
    )

    teachers = _agg(
        db.query_all("SELECT id, name, thumbnail, introduction FROM coach"),
        ("id", "name", "thumbnail", "introduction"),
    )

    market = db.query_one(
        "SELECT id, slogan FROM market ORDER BY updated_time DESC LIMIT 1"
    )
    market = {"id": market["id"], "slogan": market["slogan"]} if market else None

    notices = _agg(
        db.query_all(
            "SELECT id, title, updated_time FROM announcement ORDER BY updated_time DESC LIMIT 3"
        ),
        ("id", "title", "updated_time"),
    )

    return {
        "poster": poster,
        "booked": booked,
        "actions": actions,
        "teachers": teachers,
        "market": market,
        "notices": notices,
    }
