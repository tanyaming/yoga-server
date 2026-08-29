"""路由：登录 / 首页 / 约课（用户端核心）。路径与原 Rust 后端一一对应。"""
from fastapi import APIRouter, Request

from .. import wechat
from ..responses import int_resp, json_resp
from ..services import booking, index_service

router = APIRouter()


@router.get("/yoga/auth")
async def auth(code: str):
    """微信登录：代理 code2session，原样返回微信 JSON。"""
    text = await wechat.jscode2session(code)
    return json_resp(_loads(text))


@router.get("/yoga/index")
async def index():
    """首页聚合数据。"""
    return json_resp(index_service.index())


@router.get("/yoga/lessons")
async def lessons(start: int, openid: str, class_type: int):
    """某日课程列表（start = 北京时间当天 0 点的时间戳）。"""
    return json_resp(booking.lessons_next_two_weeks(start, openid, class_type))


@router.get("/yoga/book")
async def book(id: int, openid: str):
    """预约课程。"""
    return int_resp(booking.book(id, openid))


@router.get("/yoga/unbook")
async def unbook(id: int, openid: str):
    """取消预约（仅本人）。"""
    return int_resp(booking.unbook(id, openid))


def _loads(text):
    import json

    try:
        return json.loads(text)
    except ValueError:
        return {"errcode": -1, "errmsg": "weixin api error", "raw": text}
