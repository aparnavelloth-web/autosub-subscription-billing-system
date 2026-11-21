from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import random
from typing import Any, Dict, Optional
import uuid
from app.core.config import settings
from app.models.enums import PaymentMethod, PaymentStatus, RefundStatus
from app.schemas.payment import MockPaymentOutcome


class InvalidWebhookSignatureError(Exception):
    """Raised when a webhook signature header is missing, malformed, expired, or invalid."""


@dataclass(frozen=True)
class MockChargeResult:
    """Structured result returned by MockPaymentGateway.charge()."""
    transaction_reference: str
    status: PaymentStatus
    processed_at: Optional[datetime]
    failure_reason: Optional[str]
    gateway_response: Dict[str, Any]


@dataclass(frozen=True)
class MockRefundResult:
    """Structured result returned by MockPaymentGateway.refund()."""
    refund_reference: str
    status: RefundStatus
    refunded_at: Optional[datetime]
    failure_reason: Optional[str]
    gateway_response: Dict[str, Any]


class MockPaymentGateway:
    """
    In-memory deterministic payment gateway simulator for local and test workflows.
    Never connects to external payment processors or handles real funds.
    """

    GATEWAY_NAME = "mock_gateway"
    SUCCESS_PROBABILITY: float = 0.80

    @classmethod
    def determine_automatic_outcome(
        cls,
        random_value: Optional[float] = None,
    ) -> MockPaymentOutcome:
        """
        Calculates payment outcome automatically based on 80% success / 20% failure probability.
        Allows injecting random_value (float 0.0 to 1.0) or mocking random.random for unit testing.
        Returns MockPaymentOutcome.SUCCEED if val < 0.80, else MockPaymentOutcome.FAIL.
        """
        val = random_value if random_value is not None else random.random()
        return MockPaymentOutcome.SUCCEED if val < cls.SUCCESS_PROBABILITY else MockPaymentOutcome.FAIL

    @staticmethod
    def generate_transaction_reference() -> str:
        """Generates a unique gateway transaction reference."""
        return f"TXN-{uuid.uuid4().hex[:16].upper()}"

    @staticmethod
    def generate_refund_reference() -> str:
        """Generates a unique gateway refund reference."""
        return f"REF-{uuid.uuid4().hex[:16].upper()}"

    @classmethod
    def charge(
        cls,
        *,
        amount: Decimal,
        currency: str,
        payment_method: PaymentMethod = PaymentMethod.SIMULATED,
        transaction_reference: Optional[str] = None,
        outcome: MockPaymentOutcome = MockPaymentOutcome.SUCCEED,
        failure_reason: Optional[str] = None,
    ) -> MockChargeResult:
        """
        Simulates a payment charge attempt returning a succeeded, failed, or pending outcome.
        """
        now = datetime.now(timezone.utc)
        txn_ref = transaction_reference or cls.generate_transaction_reference()

        if outcome == MockPaymentOutcome.AUTO:
            outcome = cls.determine_automatic_outcome()

        if outcome == MockPaymentOutcome.SUCCEED:
            auth_code = f"AUTH-{uuid.uuid4().hex[:8].upper()}"
            return MockChargeResult(
                transaction_reference=txn_ref,
                status=PaymentStatus.SUCCEEDED,
                processed_at=now,
                failure_reason=None,
                gateway_response={
                    "gateway": cls.GATEWAY_NAME,
                    "simulated": True,
                    "outcome": "succeeded",
                    "transaction_reference": txn_ref,
                    "auth_code": auth_code,
                    "amount": str(amount),
                    "currency": currency,
                    "payment_method": payment_method.value,
                    "processed_at": now.isoformat(),
                },
            )

        if outcome == MockPaymentOutcome.FAIL:
            reason = failure_reason or "Simulated payment decline by mock payment gateway."
            return MockChargeResult(
                transaction_reference=txn_ref,
                status=PaymentStatus.FAILED,
                processed_at=None,
                failure_reason=reason,
                gateway_response={
                    "gateway": cls.GATEWAY_NAME,
                    "simulated": True,
                    "outcome": "failed",
                    "decline_code": "simulated_decline",
                    "failure_reason": reason,
                    "transaction_reference": txn_ref,
                    "amount": str(amount),
                    "currency": currency,
                    "payment_method": payment_method.value,
                    "attempted_at": now.isoformat(),
                },
            )

        if outcome == MockPaymentOutcome.PENDING:
            pending_note = failure_reason or "Awaiting asynchronous gateway settlement confirmation."
            return MockChargeResult(
                transaction_reference=txn_ref,
                status=PaymentStatus.PENDING,
                processed_at=None,
                failure_reason=None,
                gateway_response={
                    "gateway": cls.GATEWAY_NAME,
                    "simulated": True,
                    "outcome": "pending",
                    "pending_reason": pending_note,
                    "transaction_reference": txn_ref,
                    "amount": str(amount),
                    "currency": currency,
                    "payment_method": payment_method.value,
                    "initiated_at": now.isoformat(),
                },
            )

        raise ValueError(f"Unsupported mock gateway outcome: {outcome}")

    @classmethod
    def refund(
        cls,
        *,
        amount: Decimal,
        currency: str,
        transaction_reference: str,
        refund_reference: Optional[str] = None,
        outcome: MockPaymentOutcome = MockPaymentOutcome.SUCCEED,
        reason: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> MockRefundResult:
        """
        Simulates a payment refund request returning a succeeded, failed, or pending outcome.
        Never communicates with real external payment providers.
        """
        now = datetime.now(timezone.utc)
        ref_id = refund_reference or cls.generate_refund_reference()

        if outcome == MockPaymentOutcome.SUCCEED:
            return MockRefundResult(
                refund_reference=ref_id,
                status=RefundStatus.SUCCEEDED,
                refunded_at=now,
                failure_reason=None,
                gateway_response={
                    "gateway": cls.GATEWAY_NAME,
                    "simulated": True,
                    "type": "refund",
                    "outcome": "succeeded",
                    "refund_reference": ref_id,
                    "transaction_reference": transaction_reference,
                    "amount": str(amount),
                    "currency": currency,
                    "reason": reason,
                    "processed_at": now.isoformat(),
                },
            )

        if outcome == MockPaymentOutcome.FAIL:
            fail_msg = failure_reason or "Simulated refund failure by mock payment gateway."
            return MockRefundResult(
                refund_reference=ref_id,
                status=RefundStatus.FAILED,
                refunded_at=None,
                failure_reason=fail_msg,
                gateway_response={
                    "gateway": cls.GATEWAY_NAME,
                    "simulated": True,
                    "type": "refund",
                    "outcome": "failed",
                    "decline_code": "simulated_refund_failed",
                    "failure_reason": fail_msg,
                    "refund_reference": ref_id,
                    "transaction_reference": transaction_reference,
                    "amount": str(amount),
                    "currency": currency,
                    "reason": reason,
                    "attempted_at": now.isoformat(),
                },
            )

        if outcome == MockPaymentOutcome.PENDING:
            pending_note = failure_reason or "Awaiting asynchronous gateway refund confirmation."
            return MockRefundResult(
                refund_reference=ref_id,
                status=RefundStatus.PENDING,
                refunded_at=None,
                failure_reason=None,
                gateway_response={
                    "gateway": cls.GATEWAY_NAME,
                    "simulated": True,
                    "type": "refund",
                    "outcome": "pending",
                    "pending_reason": pending_note,
                    "refund_reference": ref_id,
                    "transaction_reference": transaction_reference,
                    "amount": str(amount),
                    "currency": currency,
                    "reason": reason,
                    "initiated_at": now.isoformat(),
                },
            )

        raise ValueError(f"Unsupported mock refund outcome: {outcome}")

    @staticmethod
    def sign_webhook_payload(
        raw_body: bytes,
        *,
        secret: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> str:
        """
        Generates an HMAC-SHA256 webhook signature header value in the form:
        `t=<unix_timestamp>,v1=<hex_digest>` over `<timestamp>.<raw_body>`.
        """
        signing_secret = secret if secret is not None else settings.MOCK_PAYMENT_WEBHOOK_SECRET
        ts = int(timestamp if timestamp is not None else datetime.now(timezone.utc).timestamp())
        signed_message = f"{ts}.".encode("utf-8") + raw_body
        digest = hmac.new(signing_secret.encode("utf-8"), signed_message, hashlib.sha256).hexdigest()
        return f"t={ts},v1={digest}"

    @staticmethod
    def verify_webhook_signature(
        *,
        raw_body: bytes,
        signature_header: Optional[str],
        secret: Optional[str] = None,
        tolerance_seconds: Optional[int] = None,
        now_timestamp: Optional[int] = None,
    ) -> int:
        """
        Verifies an incoming `X-Mock-Signature` header (`t=<ts>,v1=<hmac_sha256_hex>`)
        against the exact raw request body bytes using constant-time hmac.compare_digest
        and enforces replay-window timestamp validation. Returns the verified Unix timestamp.
        """
        if not signature_header or not signature_header.strip():
            raise InvalidWebhookSignatureError("Missing webhook signature header.")

        signing_secret = secret if secret is not None else settings.MOCK_PAYMENT_WEBHOOK_SECRET
        if not signing_secret:
            raise InvalidWebhookSignatureError("Webhook signing secret is not configured.")

        parts: Dict[str, str] = {}
        for segment in signature_header.split(","):
            if "=" not in segment:
                continue
            key, val = segment.split("=", 1)
            parts[key.strip()] = val.strip()

        ts_str = parts.get("t")
        provided_sig = parts.get("v1")
        if not ts_str or not provided_sig:
            raise InvalidWebhookSignatureError("Malformed webhook signature header; expected 't=<ts>,v1=<sig>'.")

        try:
            ts = int(ts_str)
        except ValueError as exc:
            raise InvalidWebhookSignatureError("Invalid webhook signature timestamp.") from exc

        tolerance = (
            tolerance_seconds
            if tolerance_seconds is not None
            else settings.MOCK_PAYMENT_WEBHOOK_TOLERANCE_SECONDS
        )
        current_ts = int(now_timestamp if now_timestamp is not None else datetime.now(timezone.utc).timestamp())
        if abs(current_ts - ts) > tolerance:
            raise InvalidWebhookSignatureError(
                f"Webhook timestamp {ts} is outside allowed replay window (+/- {tolerance}s)."
            )

        signed_message = f"{ts}.".encode("utf-8") + raw_body
        expected_sig = hmac.new(signing_secret.encode("utf-8"), signed_message, hashlib.sha256).hexdigest()

        if len(provided_sig) != len(expected_sig) or not hmac.compare_digest(
            expected_sig, provided_sig.lower()
        ):
            raise InvalidWebhookSignatureError("Webhook signature verification failed.")

        return ts
