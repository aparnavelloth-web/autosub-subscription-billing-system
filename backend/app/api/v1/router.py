from fastapi import APIRouter
from app.api.v1.endpoints import auth, customers, health, invoices, payments, plans, subscriptions, webhooks

api_router = APIRouter()
api_router.include_router(health.router, tags=["System Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(plans.router, prefix="/plans", tags=["Plans"])
api_router.include_router(customers.router, prefix="/customers", tags=["Customers"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["Subscriptions"])
api_router.include_router(invoices.router, prefix="/invoices", tags=["Invoices"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
