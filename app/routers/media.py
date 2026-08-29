"""路由：周课表图片生成 + 图片上传。

移植自 Rust schedule.rs 的绘制逻辑：
- 模板 pattern.png，字体 PingFang.ttf
- 上午课（start_time==32400 即 9:00）画在上半区 y=311，其余画在 y=446
- 列 x = 202 + 135*星期偏移(周一=0)，文本水平居中（宽 100 内）
"""
import io
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont

from .. import config
from ..services import booking

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PATTERN = BASE_DIR / "pattern.png"
FONT = BASE_DIR / "PingFang.ttf"

WEEK_CHARS = "一二三四五六日"


@router.get("/yoga/admin/schedule")
async def admin_schedule():
    lessons = booking.query_week_lessons()
    image = Image.open(PATTERN).convert("RGBA")
    draw = ImageDraw.Draw(image)
    font_week = ImageFont.truetype(str(FONT), 36)
    for lesson in lessons:
        date_time = lesson["date_time"]
        start_time = lesson["start_time"]
        # date_time 为北京时间 0 点的 UTC 时间戳 → +8h 得到本地日期
        ts = date_time + 28800
        import datetime as _dt

        d = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date()
        offset = d.weekday()  # 周一=0
        top = start_time == 32400
        y_name = 311 if top else 446
        if top:
            _draw_centered(
                draw, font_week, f"周{WEEK_CHARS[offset]}", offset, 186
            )
        name = lesson["lesson_name"] or ""
        font_name = ImageFont.truetype(str(FONT), 24 if len(name) > 4 else 28)
        _draw_centered(draw, font_name, name, offset, y_name)
        font_teacher = ImageFont.truetype(str(FONT), 22)
        _draw_centered(draw, font_teacher, lesson["teacher_name"] or "", offset, y_name + 32)

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


def _draw_centered(draw: ImageDraw.ImageDraw, font, text: str, x_index: int, y: int):
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    draw.text((202 + 135 * x_index + (100 - width) / 2, y), text, font=font, fill=(255, 255, 255, 255))


@router.post("/yoga/picture")
async def picture(file: UploadFile = File(...)):
    """图片上传（原图 Rust 版是空实现，这里保存到 IMAGE_DIR 并返回文件名）。"""
    return await _save(file)


@router.post("/yoga/avatar")
async def avatar(file: UploadFile = File(...)):
    """头像上传，保存为 <name>.avif，返回文件名。"""
    return await _save(file, suffix=".avif")


async def _save(file: UploadFile, suffix: str = ""):
    name = file.filename or "upload"
    path = Path(config.IMAGE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    target = path / (name + suffix)
    content = await file.read()
    target.write_bytes(content)
    return Response(content=target.name, media_type="text/plain")
