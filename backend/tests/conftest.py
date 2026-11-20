"""
Pytest configuration and isolated test database fixtures.
Strictly targets the dedicated PostgreSQL test database (subscription_billing_test_db)
and refuses to run if pointed at the development database (subscription_billing_db).
"""
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient
from app.core.config import settings
from app.database.session import get_db
from app.main import app


BACKEND_DIR = Path(__file__).resolve().parent.parent


def assert_safe_test_database(database_url: str) -> None:
    """
    Safety guard preventing tests or destructive test cleanup from ever executing
    against the development/production database (subscription_billing_db).
    """
    parsed = make_url(database_url)
    db_name = parsed.database or ""
    if db_name == "subscription_billing_db" or "test" not in db_name.lower():
        raise RuntimeError(
            f"SAFETY ABORT: Refusing to execute test suite against non-test database '{db_name}'. "
            "Tests must target 'subscription_billing_test_db'."
        )


@pytest.fixture(scope="session")
def test_engine():
    """
    Creates a SQLAlchemy engine bound exclusively to the isolated PostgreSQL test database
    and ensures Alembic migrations are applied to head.
    """
    test_url = settings.test_database_url
    assert_safe_test_database(test_url)

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", test_url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(test_url, pool_pre_ping=True)
    assert_safe_test_database(str(engine.url))
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def session_factory(test_engine):
    """
    Provides an isolated sessionmaker bound to subscription_billing_test_db
    and cleanly resets test tables before and after each test function.
    """
    assert_safe_test_database(str(test_engine.url))
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE webhook_events, payment_refunds, payments, invoice_items, invoices, "
                "billing_cycles, subscriptions, audit_logs, customers, plans, users "
                "RESTART IDENTITY CASCADE;"
            )
        )

    Factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    yield Factory

    # Clean test database only (asserting again before truncation)
    assert_safe_test_database(str(test_engine.url))
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE webhook_events, payment_refunds, payments, invoice_items, invoices, "
                "billing_cycles, subscriptions, audit_logs, customers, plans, users "
                "RESTART IDENTITY CASCADE;"
            )
        )


@pytest.fixture(scope="function")
def db_session(session_factory) -> Session:
    """
    Yields a fresh SQLAlchemy Session bound exclusively to subscription_billing_test_db.
    """
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="function")
def api_client(session_factory) -> TestClient:
    """
    Provides a FastAPI TestClient whose get_db dependency is strictly wired to
    subscription_billing_test_db.
    """
    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
