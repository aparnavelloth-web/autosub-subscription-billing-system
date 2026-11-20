from typing import Optional, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class DatabaseHealth(BaseModel):
    status: str = Field(..., description="Database connection status ('connected' or 'disconnected')")
    dialect: str = Field(..., description="Database dialect in use (e.g., postgresql, sqlite)")
    database: Optional[str] = Field(None, description="Target database name if connected")
    error: Optional[str] = Field(None, description="Diagnostic error message if disconnected")


class HealthCheckResponse(BaseModel):
    status: str = Field(default="healthy", description="Application service status")
    app_name: str = Field(..., description="Project name")
    version: str = Field(..., description="Application version")
    environment: str = Field(..., description="Running environment (development, staging, production)")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Current server UTC timestamp",
    )
    database: DatabaseHealth = Field(..., description="PostgreSQL database connectivity details")
