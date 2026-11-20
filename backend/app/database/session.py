from typing import Generator
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings

logger = logging.getLogger(__name__)

connect_args = {}
url = settings.sync_database_url
if url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = None
SessionLocal = None
_db_driver_error = None

try:
    engine = create_engine(
        url,
        echo=settings.DEBUG and settings.ENVIRONMENT == "development",
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )
except (ImportError, ModuleNotFoundError) as err:
    logger.warning(
        "Database driver error: %s. PostgreSQL driver 'psycopg2' may not be installed. "
        "Falling back to in-memory SQLite for non-blocking local operation.",
        err,
    )
    _db_driver_error = str(err)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except Exception as err:
    logger.warning("Database engine creation error: %s", err)
    _db_driver_error = str(err)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a SQLAlchemy database session per request
    and guarantees its closure upon request completion.
    """
    if SessionLocal is None:
        raise RuntimeError("Database session factory is not configured.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> dict:
    """
    Verifies live database connectivity using a lightweight query.
    Returns status and diagnostic details without throwing unhandled exceptions.
    """
    if _db_driver_error:
        return {
            "status": "disconnected",
            "dialect": "postgresql" if "postgresql" in url else "unknown",
            "error": f"Database driver not installed: {_db_driver_error}. Run: pip install -r requirements.txt",
        }

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "connected",
            "dialect": engine.dialect.name,
            "database": settings.POSTGRES_DB if not settings.DATABASE_URL else "custom_url",
        }
    except Exception as exc:
        logger.warning("Database connectivity check failed: %s", exc)
        return {
            "status": "disconnected",
            "dialect": engine.dialect.name if engine else "postgresql",
            "error": "Database service is unreachable or not yet initialized.",
        }
