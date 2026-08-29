"""客户端信息上报：fn_debug（含字段校验，非法返回 -1）。"""
import re
import time

from .. import db

RE_OPENID = re.compile(r"^[a-zA-Z0-9_-]{28}$")
RE_SDK = re.compile(r"^\d+\.\d+\.\d$")
RE_WIDTH = re.compile(r"^\d+$")
RE_RATIO = re.compile(r"^[\d.]+$")
BAD_MODEL = "iPhone XS MAX China-exclusive<iPhone 11,6>"


def debug(obj: dict, ip: str) -> int:
    open_id = obj.get("open_id") or ""
    if (
        not RE_OPENID.match(open_id)
        or not RE_SDK.match(obj.get("sdk_version") or "")
        or not RE_WIDTH.match(str(obj.get("screen_width") or ""))
        or not RE_RATIO.match(str(obj.get("pixel_ratio") or ""))
        or obj.get("model") == BAD_MODEL
    ):
        return -1

    now = int(time.time())
    existing = db.query_one(
        "SELECT id FROM systeminfo WHERE open_id = %s LIMIT 1", (open_id,)
    )
    if existing:
        db.execute(
            """
            UPDATE systeminfo SET
                sdk_version = COALESCE(%s, sdk_version),
                brand = COALESCE(%s, brand),
                model = COALESCE(%s, model),
                pixel_ratio = COALESCE(%s, pixel_ratio),
                platform = COALESCE(%s, platform),
                screen_height = COALESCE(%s, screen_height),
                screen_width = COALESCE(%s, screen_width),
                version = COALESCE(%s, version),
                ip = COALESCE(%s, ip),
                `count` = `count` + 1,
                updated_time = %s
            WHERE id = %s
            """,
            (
                obj.get("sdk_version"), obj.get("brand"), obj.get("model"),
                obj.get("pixel_ratio"), obj.get("platform"),
                obj.get("screen_height"), obj.get("screen_width"),
                obj.get("version"), ip, now, existing["id"],
            ),
        )
        return existing["id"]

    max_row = db.query_one("SELECT COALESCE(MAX(id), 0) AS m FROM systeminfo")
    new_id = int(obj.get("id") or 0) or max_row["m"] + 1
    db.execute(
        """
        INSERT INTO systeminfo (id, open_id, sdk_version, brand, model, pixel_ratio,
                                platform, screen_height, screen_width, version, ip,
                                `count`, creation_time, updated_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            new_id, open_id, obj.get("sdk_version"), obj.get("brand"), obj.get("model"),
            obj.get("pixel_ratio"), obj.get("platform"), obj.get("screen_height"),
            obj.get("screen_width"), obj.get("version"), ip,
            int(obj.get("count") or 1), now, now,
        ),
    )
    return new_id
