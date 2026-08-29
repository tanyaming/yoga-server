"""路由：管理端课程 / 会员管理。"""
import json

from fastapi import APIRouter, Request

from ..responses import int_resp, json_resp
from ..services import admin

router = APIRouter()


@router.get("/yoga/admin/lessons")
async def admin_lessons(start: int, end: int):
    return json_resp(admin.admin_lessons(start, end))


@router.get("/yoga/admin/lesson")
async def admin_lesson(id: int):
    return json_resp(admin.admin_lesson(id))


@router.get("/yoga/admin/lesson/hidden")
async def admin_lesson_hidden(id: int, status: int):
    return int_resp(admin.admin_lesson_update_status(id, status))


@router.get("/yoga/admin/lesson/delete")
async def admin_lesson_delete(id: int):
    return int_resp(admin.admin_lesson_delete(id))


@router.get("/yoga/admin/lessons/and/teachers")
async def admin_lessons_and_teachers(id: int):
    return json_resp(admin.admin_lessons_and_teachers(id))


@router.post("/yoga/lesson/update")
async def admin_lesson_update(request: Request):
    """按周系列修改课程。"""
    obj = _parse_body(await request.body())
    if obj is None:
        return int_resp(-1)
    return int_resp(admin.admin_lesson_update(obj))


@router.post("/yoga/admin/lessons/update")
async def admin_lessons_update(request: Request, open_id: str = ""):
    """按星期批量排课（一年）。"""
    obj = _parse_body(await request.body())
    if obj is None:
        return int_resp(-1)
    return int_resp(admin.admin_lessons_update(obj))


@router.get("/yoga/admin/user/lessons")
async def admin_user_lessons(id: int, start: int, end: int, open_id: str = ""):
    return json_resp(admin.admin_user_lessons(id, start, end))


@router.get("/yoga/admin/users/all")
async def admin_users_all(open_id: str):
    return json_resp(admin.admin_users_all(open_id))


@router.get("/yoga/admin/user")
async def admin_user(open_id: str = "", id: int = 0):
    return json_resp(admin.admin_user(id))


def _parse_body(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
