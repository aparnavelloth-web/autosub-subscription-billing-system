from fastapi import APIRouter, status
from app.core.config import settings
from app.schemas.health import HealthCheckResponse, DatabaseHealth
from app.database.session import check_db_connection

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Application & Database Health Check",
    description="Returns live system operational health, application metadata, and database connectivity status.",
)
def get_health() -> HealthCheckResponse:
    db_info = check_db_connection()
    return HealthCheckResponse(
        status="healthy",
        app_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
        database=DatabaseHealth(**db_info),
    )

