"""配置：优先读 .env 文件，其次环境变量。敏感信息不进代码仓库。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """简单 .env 加载器：KEY=VALUE，# 开头为注释。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "yuekeDB")

# 微信小程序凭证（上线前必须替换成自己的）
APPID = os.getenv("APPID", "")
SECRET = os.getenv("SECRET", "")

# 上传文件保存目录
IMAGE_DIR = os.getenv("IMAGE_DIR", str(BASE_DIR / "images"))

# 服务监听（与原 Rust 后端一致）
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8002"))
