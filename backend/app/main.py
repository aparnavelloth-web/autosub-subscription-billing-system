from contextlib import asynccontextmanager
import logging
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.core.config import settings
from app.database.session import get_db
from app.api.v1.router import api_router
from app.api.v1.endpoints.health import get_health
from app.api.v1.endpoints.payments import process_payment
from app.api.v1.endpoints.webhooks import handle_mock_payment_webhook
from app.schemas.payment import PaymentProcessRequest, PaymentResponse, WebhookProcessResponse

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("subscription_billing")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s in %s mode...", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)
    logger.info("CORS origins configured: %s", settings.CORS_ORIGINS)
    yield
    logger.info("Shutting down %s...", settings.PROJECT_NAME)


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan,
)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(api_router, prefix=settings.API_V1_STR)

# Direct alias for Milestone 2 Task 3 requirement: POST /payments/process
@app.post(
    "/payments/process",
    response_model=PaymentResponse,
    status_code=201,
    tags=["Payments"],
    summary="Process Invoice Payment (Root Alias)",
    description="Direct top-level alias for Milestone 2 Task 3: POST /payments/process.",
)
def root_process_payment(
    payload: PaymentProcessRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> PaymentResponse:
    return process_payment(payload=payload, request=request, response=response, db=db)


# Direct alias for Milestone 2 Task 3 requirement: POST /webhooks/payment
@app.post(
    "/webhooks/payment",
    response_model=WebhookProcessResponse,
    status_code=200,
    tags=["Webhooks"],
    summary="Receive Payment Webhook (Root Alias)",
    description="Direct top-level alias for Milestone 2 Task 3: POST /webhooks/payment.",
)
async def root_payment_webhook(
    request: Request,
    x_mock_signature: str | None = Header(default=None, alias="X-Mock-Signature"),
    db: Session = Depends(get_db),
) -> WebhookProcessResponse:
    return await handle_mock_payment_webhook(
        request=request,
        x_mock_signature=x_mock_signature,
        db=db,
    )


# Top-level direct health check and root info endpoints for convenience
@app.get("/health", include_in_schema=False)
def direct_health():
    return get_health()


@app.get("/", tags=["Root"])
def root_info():
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": f"{settings.API_V1_STR}/docs",
        "health_check": f"{settings.API_V1_STR}/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )

