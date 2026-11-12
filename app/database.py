"""数据库引擎、会话与事务边界。Repository 层强制租户收口见 repositories/base.py。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .logging_config import get_logger

logger = get_logger('app.database')

_is_sqlite = settings.DATABASE_URL.startswith('sqlite')

if _is_sqlite:
    engine = create_engine(
        settings.DATABASE_URL,
        echo=settings.DB_ECHO,
        connect_args={'check_same_thread': False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, 'connect')
    def _sqlite_pragmas(dbapi_conn, _record):
        """SQLite 连接级 PRAGMA：并发屏障 + 外键一致性。

        - ``foreign_keys=ON``：与 MySQL 行为对齐，脏引用立即报错；
        - ``journal_mode=WAL``：读写不互斥，并发写不再批量 ``database is locked``；
        - ``busy_timeout=8000``：写锁竞争时自旋等待 8s，而不是立刻抛错；
        - ``synchronous=NORMAL``：WAL 下兼顾安全与吞吐（崩溃最多丢最后一个提交）。

        生产环境禁止使用 SQLite（见 config.validate），因此这里的取舍只影响开发/测试。
        """
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA foreign_keys=ON')
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=8000')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.close()
else:
    engine = create_engine(
        settings.DATABASE_URL,
        echo=settings.DB_ECHO,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=3600,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话，异常自动回滚。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """服务层/脚本使用：显式事务边界，异常回滚，正常提交。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """开发环境建表；生产一律走 alembic upgrade head。"""
    from .models import Base  # noqa: F401  触发全部模型注册
    Base.metadata.create_all(bind=engine)
    logger.info('数据库表已就绪', extra={'extra': {'driver': 'sqlite' if _is_sqlite else 'mysql'}})
