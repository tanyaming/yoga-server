"""本地启动入口：python run.py"""
import uvicorn

from app.config import settings

if __name__ == '__main__':
    settings.validate()
    uvicorn.run(
        'app.main:app',
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_ENV == 'dev',
        log_level=settings.LOG_LEVEL.lower(),
        access_log=False,
    )
