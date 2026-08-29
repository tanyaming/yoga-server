"""用户服务：fn_user_query / fn_user_update / fn_user_book_statistics。"""
import time

from .. import db


def user_query(openid: str):
    """按 openid 查用户公开资料。不存在返回 None。"""
    r = db.query_one(
        """
        SELECT u.id, u.avatar_url, u.nick_name, u.user_type
        FROM `user` u WHERE u.open_id = %s LIMIT 1
        """,
        (openid,),
    )
    if not r:
        return None
    return {
        "id": r["id"],
        "avatar_url": r["avatar_url"],
        "nick_name": r["nick_name"],
        "user_type": r["user_type"],
    }


def _parse_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def user_update(obj: dict) -> int:
    """注册/更新学员资料（按 open_id upsert），返回用户 id。

    字段为 null/缺失时保持原值，与 PG 的 coalesce 语义一致。
    """
    openid = obj.get("open_id")
    if not openid:
        return -1

    now = int(time.time())
    creation_time = _parse_int(obj.get("create_at") or obj.get("creation_time"), 0) or now

    existing = db.query_one(
        "SELECT id FROM `user` WHERE open_id = %s LIMIT 1", (openid,)
    )
    if existing:
        user_id = existing["id"]
        sets, params = [], []
        for field in ("address", "avatar_url", "name", "nick_name", "note", "phone"):
            if obj.get(field) is not None:
                sets.append(f"`{field}` = %s")
                params.append(str(obj[field]))
        for field in ("gender", "user_type"):
            if _parse_int(obj.get(field), 0) != 0:
                sets.append(f"`{field}` = %s")
                params.append(_parse_int(obj[field]))
        sets.append("updated_time = %s")
        params.append(now)
        if sets:
            params.append(user_id)
            db.execute(
                f"UPDATE `user` SET {', '.join(sets)} WHERE id = %s", tuple(params)
            )
        return user_id

    # 新用户：id 取传入值或 max(id)+1
    new_id = _parse_int(obj.get("id"), 0)
    if new_id == 0:
        max_row = db.query_one("SELECT COALESCE(MAX(id), 0) AS m FROM `user`")
        new_id = max_row["m"] + 1
    db.execute(
        """
        INSERT INTO `user` (id, address, avatar_url, gender, name, nick_name, note,
                            open_id, phone, user_type, creation_time, updated_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            new_id,
            obj.get("address"),
            obj.get("avatar_url"),
            _parse_int(obj.get("gender")),
            obj.get("name"),
            obj.get("nick_name") or "",
            obj.get("note"),
            openid,
            obj.get("phone"),
            _parse_int(obj.get("user_type")),
            creation_time,
            now,
        ),
    )
    return new_id


def user_book_statistics(openid: str):
    """学员约课统计：团课 big / 私教 one / 小班 small。"""
    r = db.query_one(
        """
        SELECT u.id, u.avatar_url, u.nick_name, u.user_type,
               SUM(c.class_type = 4) AS big,
               SUM(c.class_type = 2) AS `one`,
               SUM(c.class_type = 1) AS `small`
        FROM `user` u
                 LEFT JOIN reservation r ON u.id = r.user_id
                 LEFT JOIN course c ON c.id = r.course_id
        WHERE u.open_id = %s
        GROUP BY u.id
        """,
        (openid,),
    )
    if not r:
        return None
    return {
        "id": r["id"],
        "avatar_url": r["avatar_url"],
        "nick_name": r["nick_name"],
        "user_type": r["user_type"],
        "big": int(r["big"] or 0),
        "one": int(r["one"] or 0),
        "small": int(r["small"] or 0),
    }
