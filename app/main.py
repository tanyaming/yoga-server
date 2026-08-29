"""FastAPI 入口：挂载全部路由，兼容原 Rust 后端的 24 个接口。"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routers import admin, core, media, user

app = FastAPI(title="晨蕴瑜伽约课后端", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core.router)
app.include_router(user.router)
app.include_router(admin.router)
app.include_router(media.router)


@app.get("/favicon.ico")
async def favicon():
    raise HTTPException(status_code=404)


@app.get("/ping")
async def ping():
    """健康检查（新增）。"""
    return {"status": "ok"}


# 管理后台静态页面：/admin → web/index.html
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/admin", StaticFiles(directory=str(WEB_DIR), html=True), name="admin")

# 上传图片静态服务：/images → IMAGE_DIR（对应原部署中 Nginx 的 /images 静态目录）
from . import config  # noqa: E402

IMAGE_PATH = Path(config.IMAGE_DIR)
IMAGE_PATH.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGE_PATH)), name="images")
