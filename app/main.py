"""应用装配：中间件、异常处理、路由与健康检查。"""
from __future__ import annotations

from fastapi import FastAPI

from app.observability.middleware import ObservabilityMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="api-observability")
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"Hello": "World"}

    return app


app = create_app()
