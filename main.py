"""服务入口：`uvicorn main:app` 或 `uvicorn app.main:app`。"""
from app.main import app

__all__ = ["app"]
