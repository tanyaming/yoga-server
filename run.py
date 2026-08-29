#!/usr/bin/env python3
"""启动：python run.py 或 uvicorn app.main:app"""
import uvicorn

from app import config

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=False)
