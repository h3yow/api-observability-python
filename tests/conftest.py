from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.health import health_state
from app.logging_config import configure_logging
from app.main import app
from app.metrics import registry

# 单测日志器：用于打印输入与判定依据。
test_logger = logging.getLogger("test.observability")
test_logger.setLevel(logging.INFO)
if not test_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[test] %(message)s"))
    test_logger.addHandler(_handler)


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后重置指标与健康检查，避免用例间污染。"""
    configure_logging()
    saved_checks = dict(health_state._readiness_checks)
    registry.reset()
    yield
    registry.reset()
    health_state._readiness_checks.clear()
    health_state._readiness_checks.update(saved_checks)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)
