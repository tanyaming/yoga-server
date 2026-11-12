"""集中配置：全部来自环境变量，启动时一次性校验、快速失败。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

# 加载 .env（不覆盖已存在的环境变量，systemd/容器注入优先）
load_dotenv()


def _env(key: str, default: str = '') -> str:
    return os.environ.get(key, default).strip()


@dataclass(frozen=True)
class Settings:
    APP_ENV: str = field(default_factory=lambda: _env('APP_ENV', 'dev'))
    APP_HOST: str = field(default_factory=lambda: _env('APP_HOST', '127.0.0.1'))
    APP_PORT: int = field(default_factory=lambda: int(_env('APP_PORT', '8002')))
    LOG_LEVEL: str = field(default_factory=lambda: _env('LOG_LEVEL', 'INFO'))
    CORS_ORIGINS_RAW: str = field(default_factory=lambda: _env('CORS_ORIGINS', 'http://127.0.0.1:8002'))
    DATABASE_URL: str = field(default_factory=lambda: _env('DATABASE_URL', 'sqlite:///./yoga.db'))
    DB_ECHO: bool = field(default_factory=lambda: _env('DB_ECHO', 'false').lower() == 'true')
    DB_POOL_SIZE: int = field(default_factory=lambda: int(_env('DB_POOL_SIZE', '10')))
    JWT_SECRET: str = field(default_factory=lambda: _env('JWT_SECRET', ''))
    JWT_ALGORITHM: str = field(default_factory=lambda: _env('JWT_ALGORITHM', 'HS256'))
    ACCESS_TOKEN_MINUTES: int = field(default_factory=lambda: int(_env('ACCESS_TOKEN_MINUTES', '30')))
    REFRESH_TOKEN_DAYS: int = field(default_factory=lambda: int(_env('REFRESH_TOKEN_DAYS', '14')))
    WX_APPID: str = field(default_factory=lambda: _env('WX_APPID', ''))
    WX_SECRET: str = field(default_factory=lambda: _env('WX_SECRET', ''))
    WX_MOCK: bool = field(default_factory=lambda: _env('WX_MOCK', 'true').lower() == 'true')
    BOOTSTRAP_ADMIN_USERNAME: str = field(default_factory=lambda: _env('BOOTSTRAP_ADMIN_USERNAME', 'admin'))
    BOOTSTRAP_ADMIN_PASSWORD: str = field(default_factory=lambda: _env('BOOTSTRAP_ADMIN_PASSWORD', ''))
    SEED_DEMO: bool = field(default_factory=lambda: _env('SEED_DEMO', 'true').lower() == 'true')
    # FR-17：点名允许的课程开始前后窗口（分钟），0 表示不限制（便于历史数据 / 自动化测试）
    ATTENDANCE_WINDOW_MINUTES: int = field(
        default_factory=lambda: int(_env('ATTENDANCE_WINDOW_MINUTES', '30')))
    # FR-14：候补递补后的确认锁定窗口（分钟），超时自动顺延下一位
    WAITLIST_OFFER_MINUTES: int = field(
        default_factory=lambda: int(_env('WAITLIST_OFFER_MINUTES', '15')))
    # FR-21：微信订阅消息通道。模板资质未就绪前默认关闭，开启必须显式配置模板 ID。
    WX_SUBSCRIBE_ENABLED: bool = field(
        default_factory=lambda: _env('WX_SUBSCRIBE_ENABLED', 'false').lower() == 'true')
    WX_SUBSCRIBE_TEMPLATE_ID: str = field(
        default_factory=lambda: _env('WX_SUBSCRIBE_TEMPLATE_ID', ''))

    @property
    def CORS_ORIGINS(self) -> List[str]:
        return [x.strip() for x in self.CORS_ORIGINS_RAW.split(',') if x.strip()]

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV.lower() in ('prod', 'production')

    def validate(self) -> None:
        """启动时集中校验。缺配置直接抛错，绝不带病启动。"""
        errors = []
        if self.APP_ENV not in ('dev', 'test', 'prod', 'production', 'staging'):
            errors.append('APP_ENV 必须是 dev/test/staging/prod')
        if not self.DATABASE_URL:
            errors.append('DATABASE_URL 未配置')
        if not self.JWT_SECRET or len(self.JWT_SECRET) < 16:
            errors.append('JWT_SECRET 缺失或过短（>=16 字符）')
        if self.ATTENDANCE_WINDOW_MINUTES < 0:
            errors.append('ATTENDANCE_WINDOW_MINUTES 不能为负')
        if self.WAITLIST_OFFER_MINUTES < 1:
            errors.append('WAITLIST_OFFER_MINUTES 必须 >= 1')
        if self.WX_SUBSCRIBE_ENABLED and not self.WX_SUBSCRIBE_TEMPLATE_ID:
            errors.append('开启 WX_SUBSCRIBE_ENABLED 必须同时配置 WX_SUBSCRIBE_TEMPLATE_ID')
        if self.is_prod:
            if 'change-me' in self.JWT_SECRET:
                errors.append('生产环境禁止使用示例 JWT_SECRET')
            if any(o == '*' for o in self.CORS_ORIGINS):
                errors.append('生产环境禁止 CORS 通配符')
            if self.WX_MOCK:
                errors.append('生产环境必须关闭 WX_MOCK 并配置 WX_APPID/WX_SECRET')
            if not self.WX_APPID or not self.WX_SECRET:
                errors.append('生产环境必须配置 WX_APPID / WX_SECRET')
            if self.DATABASE_URL.startswith('sqlite'):
                errors.append('生产环境禁止使用 SQLite，请切换 MySQL')
            if 'change-me' in self.BOOTSTRAP_ADMIN_PASSWORD:
                errors.append('生产环境必须替换 BOOTSTRAP_ADMIN_PASSWORD')
            if self.WX_SUBSCRIBE_ENABLED and not self.WX_APPID:
                errors.append('开启订阅消息通道必须配置 WX_APPID')
        if self.APP_PORT < 1 or self.APP_PORT > 65535:
            errors.append('APP_PORT 非法')
        if errors:
            raise RuntimeError('配置校验失败：\n  - ' + '\n  - '.join(errors))


settings = Settings()
