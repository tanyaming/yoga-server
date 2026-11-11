"""FastAPI 应用入口：中间件、全局异常、健康检查、路由注册、启动种子。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import settings
from .database import SessionLocal, init_db
from .errors import AppError
from .logging_config import get_logger, request_id_var, setup_logging
from .security import random_id

logger = get_logger('app.main')


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    setup_logging(settings.LOG_LEVEL)
    if settings.APP_ENV == 'dev':
        init_db()
    # staging 亦运行幂等种子（建表由 alembic 完成）；生产环境一律关闭
    if settings.SEED_DEMO and not settings.is_prod:
        from .seed import seed_demo
        seed_demo()
    logger.info('应用启动完成', extra={'extra': {'env': settings.APP_ENV}})
    yield


app = FastAPI(title='场馆连锁客户管理平台', version='1.0.0', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get('X-Request-Id') or random_id()[:12]
    request_id_var.set(rid)
    response = await call_next(request)
    response.headers['X-Request-Id'] = rid
    return response


# ---------- 全局异常 ----------
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict(request_id_var.get('')))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={
        'error': {'code': 'VALIDATION_ERROR', 'message': '请求参数不合法',
                  'request_id': request_id_var.get('')}})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception('未捕获异常', extra={'extra': {'path': request.url.path}})
    return JSONResponse(status_code=500, content={
        'error': {'code': 'INTERNAL_ERROR', 'message': '服务内部错误',
                  'request_id': request_id_var.get('')}})


# ---------- 健康检查 ----------
@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/ready')
def ready():
    try:
        db = SessionLocal()
        db.execute(text('SELECT 1'))
        db.close()
        return {'status': 'ready'}
    except Exception:
        return JSONResponse(status_code=503, content={'status': 'not_ready'})


# ---------- 路由注册 ----------
from .routers import app as app_router          # noqa: E402
from .routers import attendance, auth, coach, dashboard, member, payment, schedule, tenant

app.include_router(auth.router)
app.include_router(tenant.router)
app.include_router(member.router)
app.include_router(payment.router)
app.include_router(schedule.router)
app.include_router(attendance.router)
app.include_router(dashboard.router)
app.include_router(coach.router)
app.include_router(app_router.router)

# 管理后台静态站点（同源部署，免 CORS）
import os  # noqa: E402
_web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'web')
if os.path.isdir(_web_dir):
    app.mount('/admin', StaticFiles(directory=_web_dir, html=True), name='admin')
