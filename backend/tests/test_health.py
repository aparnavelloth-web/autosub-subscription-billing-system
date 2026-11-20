"""
Unit tests for Subscription Billing Automation System - Health & Configuration
"""
from app.core.config import Settings
from app.schemas.health import HealthCheckResponse, DatabaseHealth
from app.api.v1.endpoints.health import get_health
from app.main import root_info, app


def test_settings_configuration():
    test_settings = Settings(
        PROJECT_NAME="Custom Test System",
        POSTGRES_USER="testuser",
        POSTGRES_PASSWORD="testpassword",
        POSTGRES_SERVER="localhost",
        POSTGRES_PORT=5432,
        POSTGRES_DB="testdb",
        DATABASE_URL=None,
        CORS_ORIGINS=["http://localhost:5173", "http://localhost:3000"],
    )
    assert test_settings.PROJECT_NAME == "Custom Test System"
    assert test_settings.POSTGRES_USER == "testuser"
    assert "postgresql+psycopg2://" in test_settings.sync_database_url
    assert "testuser:testpassword@localhost:5432/testdb" in test_settings.sync_database_url
    assert len(test_settings.CORS_ORIGINS) == 2


def test_cors_origins_json_parsing():
    settings_json = Settings(
        CORS_ORIGINS='["http://localhost:5173", "http://127.0.0.1:5173"]'
    )
    assert isinstance(settings_json.CORS_ORIGINS, list)
    assert "http://localhost:5173" in settings_json.CORS_ORIGINS


def test_root_info_endpoint():
    res = root_info()
    assert res["project"] == "Subscription Billing Automation System"
    assert "docs_url" in res
    assert "health_check" in res


def test_health_check_endpoint():
    res = get_health()
    assert isinstance(res, HealthCheckResponse)
    assert res.status == "healthy"
    assert res.app_name == "Subscription Billing Automation System"
    assert res.version == "0.1.0"
    assert isinstance(res.database, DatabaseHealth)
    assert res.database.status in ("connected", "disconnected")


def test_fastapi_app_metadata_and_routes():
    assert app.title == "Subscription Billing Automation System"
    openapi_schema = app.openapi()
    paths = openapi_schema.get("paths", {})
    assert "/api/v1/health" in paths
    assert "/" in paths
