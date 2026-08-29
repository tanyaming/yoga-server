"""路由：用户资料 / 统计 / 教练主页 / debug 上报。"""
import json

from fastapi import APIRouter, Request

from ..responses import int_resp, json_resp
from ..services import debug_service, teacher, user_service

router = APIRouter()


@router.get("/yoga/user/query")
async def user_query(openid: str):
    return json_resp(user_service.user_query(openid))


@router.post("/yoga/user")
async def register_user(request: Request):
    """注册/更新学员资料，body 为 JSON 文本，返回用户 id（纯文本）。"""
    obj = _parse_body(await request.body())
    if obj is None:
        return int_resp(-1)
    return int_resp(user_service.user_update(obj))


@router.get("/yoga/user/book/statistics")
async def user_book_statistics(id: str):
    return json_resp(user_service.user_book_statistics(id))


@router.get("/yoga/teacher/lessons")
async def teacher_lessons(
    start_time: int, end_time: int, open_id: str, class_type: int, teacher_id: int
):
    return json_resp(
        teacher.teacher_lessons(start_time, end_time, open_id, class_type, teacher_id)
    )


@router.post("/yoga/debug")
async def debug(request: Request):
    obj = _parse_body(await request.body())
    if obj is None:
        return int_resp(-1)
    client_ip = request.headers.get("x-real-ip") or (
        request.client.host if request.client else ""
    )
    return int_resp(debug_service.debug(obj, client_ip))


def _parse_body(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
