"""微信 code2session 登录代理。"""
import httpx

from . import config


async def jscode2session(js_code: str) -> str:
    """调用微信接口，原样返回 JSON 文本（成功含 openid/session_key）。"""
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(
            "https://api.weixin.qq.com/sns/jscode2session",
            data={
                "appid": config.APPID,
                "secret": config.SECRET,
                "js_code": js_code,
                "grant_type": "authorization_code",
            },
        )
        return res.text
