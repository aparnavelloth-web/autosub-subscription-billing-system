from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.plans import router as plans_router
from app.api.v1.endpoints.customers import router as customers_router
from app.api.v1.endpoints.subscriptions import router as subscriptions_router
from app.api.v1.endpoints.invoices import router as invoices_router
from app.api.v1.endpoints.payments import router as payments_router
from app.api.v1.endpoints.webhooks import router as webhooks_router

__all__ = [
    "health_router",
    "plans_router",
    "customers_router",
    "subscriptions_router",
    "invoices_router",
    "payments_router",
    "webhooks_router",
]
