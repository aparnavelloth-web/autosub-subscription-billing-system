from decimal import Decimal
from typing import List, Union, Optional
from pathlib import Path
import json
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CURRENT_FILE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_FILE_DIR.parent.parent
ROOT_DIR = BACKEND_DIR.parent

ENV_FILES = [
    str(BACKEND_DIR / ".env"),
    str(ROOT_DIR / ".env"),
]


class Settings(BaseSettings):
    PROJECT_NAME: str = "Subscription Billing Automation System"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"

    # Server configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # PostgreSQL Database Configuration
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "subscription_billing_db"
    POSTGRES_TEST_DB: str = "subscription_billing_test_db"
    DATABASE_URL: Optional[str] = None
    TEST_DATABASE_URL: Optional[str] = None

    # Redis & Celery Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_RESULT_DB: int = 1
    REDIS_PASSWORD: Optional[str] = None
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None
    CELERY_TIMEZONE: str = "UTC"
    CELERY_TASK_ALWAYS_EAGER: bool = False
    BILLING_SCHEDULER_INTERVAL_SECONDS: int = 3600
    CELERY_TASK_MAX_RETRIES: int = 3
    CELERY_TASK_RETRY_DELAY_SECONDS: int = 60

    # Billing & Invoicing Defaults
    DEFAULT_CURRENCY: str = "INR"
    DEFAULT_TAX_RATE: Decimal = Decimal("0.18")
    DEFAULT_INVOICE_DUE_DAYS: int = 7

    # Mock Payment Gateway & Webhook Security Settings
    MOCK_PAYMENT_WEBHOOK_SECRET: str = "dev_mock_webhook_secret_change_in_prod"
    MOCK_PAYMENT_WEBHOOK_TOLERANCE_SECONDS: int = 300

    # JWT Authentication Settings
    SECRET_KEY: str = "dev_jwt_secret_key_change_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # CORS Configuration
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.ENVIRONMENT.lower() in ("production", "prod"):
            dev_patterns = ("dev_", "change_in_prod", "secret", "default", "test")
            if (
                not self.SECRET_KEY
                or any(self.SECRET_KEY.lower().startswith(p) for p in dev_patterns)
                or len(self.SECRET_KEY) < 32
            ):
                raise ValueError(
                    "CRITICAL SECURITY CONFIGURATION ERROR: Production environment cannot use a default "
                    "or weak SECRET_KEY. Please set a cryptographically secure SECRET_KEY (min 32 characters) via environment variable."
                )
            if not self.MOCK_PAYMENT_WEBHOOK_SECRET or self.MOCK_PAYMENT_WEBHOOK_SECRET == "dev_mock_webhook_secret_change_in_prod":
                raise ValueError(
                    "CRITICAL SECURITY CONFIGURATION ERROR: Production environment cannot use the default "
                    "MOCK_PAYMENT_WEBHOOK_SECRET. Set a secure secret via environment variable."
                )
        return self

    @property
    def sync_database_url(self) -> str:
        """
        Returns the SQLAlchemy connection URL.
        Uses explicit DATABASE_URL if provided, else builds from PostgreSQL components.
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def test_database_url(self) -> str:
        """
        Returns the isolated SQLAlchemy connection URL for automated tests.
        Uses explicit TEST_DATABASE_URL if provided, else builds targeting POSTGRES_TEST_DB.
        """
        if self.TEST_DATABASE_URL:
            return self.TEST_DATABASE_URL
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_TEST_DB}"
        )

    def _build_redis_url(self, db_index: int) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{db_index}"

    @property
    def celery_broker_url(self) -> str:
        """Returns the Celery message broker URL (Redis)."""
        if self.CELERY_BROKER_URL:
            return self.CELERY_BROKER_URL
        return self._build_redis_url(self.REDIS_DB)

    @property
    def celery_result_backend(self) -> str:
        """Returns the Celery result backend URL (Redis)."""
        if self.CELERY_RESULT_BACKEND:
            return self.CELERY_RESULT_BACKEND
        return self._build_redis_url(self.REDIS_RESULT_DB)

    model_config = SettingsConfigDict(
        env_file=tuple(ENV_FILES),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
