"""应用装配：中间件、异常处理、路由与健康检查。"""
from __future__ import annotations

from fastapi import FastAPI

from app.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="api-observability-python")
    app.include_router(router)
    return app


app = create_app()
