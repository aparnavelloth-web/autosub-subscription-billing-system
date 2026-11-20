import json
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.schemas.payment import MockWebhookPayload, WebhookProcessResponse
from app.services.mock_payment_gateway import InvalidWebhookSignatureError, MockPaymentGateway
from app.services.payment_service import (
    InvalidPaymentOperationError,
    PaymentNotFoundError,
    WebhookConflictError,
    WebhookService,
)

router = APIRouter()


@router.post(
    "/mock-payment",
    response_model=WebhookProcessResponse,
    status_code=status.HTTP_200_OK,
    summary="Receive Mock Payment Gateway Webhook",
    description=(
        "Verifies HMAC-SHA256 `X-Mock-Signature` (`t=<unix_ts>,v1=<hex_hmac>`) against exact raw "
        "request bytes and idempotently processes `payment.succeeded`, `payment.failed`, "
        "`refund.succeeded`, or `refund.failed` events."
    ),
)
async def handle_mock_payment_webhook(
    request: Request,
    x_mock_signature: Optional[str] = Header(default=None, alias="X-Mock-Signature"),
    db: Session = Depends(get_db),
) -> WebhookProcessResponse:
    raw_body = await request.body()

    # Verify cryptographic signature & replay timestamp window BEFORE parsing body or touching DB
    try:
        MockPaymentGateway.verify_webhook_signature(
            raw_body=raw_body,
            signature_header=x_mock_signature,
        )
    except InvalidWebhookSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    try:
        raw_payload_dict: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
        if not isinstance(raw_payload_dict, dict):
            raise ValueError("Webhook payload must be a JSON object.")
        payload = MockWebhookPayload.model_validate(raw_payload_dict)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid webhook JSON payload: {exc}",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc

    client_ip = request.client.host if request.client else None
    try:
        return WebhookService.process_mock_webhook(
            db=db,
            payload=payload,
            raw_payload_dict=raw_payload_dict,
            actor="mock_gateway_webhook",
            ip_address=client_ip,
        )
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WebhookConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidPaymentOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

