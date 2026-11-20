from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.billing_cycle import BillingCycle
from app.models.enums import BillingCycleStatus, InvoiceItemType, InvoiceStatus
from app.models.invoice import Invoice, InvoiceItem
from app.models.subscription import Subscription
from app.schemas.invoice import InvoiceItemCreate
from app.services.billing_dates import ensure_utc
from app.services.customer_service import CustomerService


class InvoiceNotFoundError(Exception):
    """Raised when a requested invoice ID does not exist."""


class BillingCycleNotFoundError(Exception):
    """Raised when a target billing cycle ID does not exist."""


class InvalidInvoiceOperationError(Exception):
    """Raised when an invoice generation or line-item operation violates domain rules."""


_CHARGE_ITEM_TYPES = {
    InvoiceItemType.BASE_SUBSCRIPTION,
    InvoiceItemType.PLAN_UPGRADE,
    InvoiceItemType.PRORATION_DEBIT,
    InvoiceItemType.USAGE,
}
_CREDIT_ITEM_TYPES = {InvoiceItemType.PRORATION_CREDIT}
_TAX_ITEM_TYPES = {InvoiceItemType.TAX}


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _serialize_invoice(invoice: Invoice) -> Dict[str, Any]:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "customer_id": invoice.customer_id,
        "customer_name": invoice.customer_name,
        "customer_email": invoice.customer_email,
        "subscription_id": invoice.subscription_id,
        "billing_cycle_id": invoice.billing_cycle_id,
        "plan_name": invoice.plan_name,
        "plan_code": invoice.plan_code,
        "subtotal": str(invoice.subtotal),
        "tax_amount": str(invoice.tax_amount),
        "discount_amount": str(invoice.discount_amount),
        "total_amount": str(invoice.total_amount),
        "amount_paid": str(invoice.amount_paid),
        "currency": invoice.currency,
        "status": invoice.status.value if invoice.status else None,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "items_count": len(invoice.items) if invoice.items is not None else 0,
    }


class InvoiceService:
    """
    Domain business service responsible for idempotent invoice generation,
    itemized charge/credit/tax calculations, unique invoice numbering, and queries.
    """

    @staticmethod
    def generate_invoice_number(db: Session, issue_date: Optional[datetime] = None) -> str:
        """
        Generates a globally unique, concurrency-safe sequential invoice number
        using PostgreSQL's atomic `invoice_number_seq` sequence (e.g., INV-2026-000001).
        """
        dt = ensure_utc(issue_date) if issue_date else datetime.now(timezone.utc)
        seq_val = db.execute(text("SELECT nextval('invoice_number_seq')")).scalar_one()
        return f"INV-{dt.year}-{int(seq_val):06d}"

    @staticmethod
    def calculate_totals_from_items(
        items: List[InvoiceItemCreate],
        tax_rate: Optional[Decimal] = None,
        extra_discount: Decimal = Decimal("0.00"),
    ) -> Tuple[Decimal, Decimal, Decimal, Decimal, List[InvoiceItemCreate]]:
        """
        Computes invoice subtotal, discount_amount, tax_amount, and total_amount
        from itemized charges, proration credits, and tax items.
        """
        if extra_discount < Decimal("0.00"):
            raise InvalidInvoiceOperationError("Discount amount cannot be negative.")
        if tax_rate is not None and tax_rate < Decimal("0.00"):
            raise InvalidInvoiceOperationError("Tax rate cannot be negative.")

        subtotal = Decimal("0.00")
        proration_credits = Decimal("0.00")
        explicit_tax_total = Decimal("0.00")
        has_explicit_tax_item = False
        finalized_items = list(items)

        for item in finalized_items:
            if item.item_type in _CHARGE_ITEM_TYPES:
                subtotal += item.amount
            elif item.item_type in _CREDIT_ITEM_TYPES:
                proration_credits += item.amount
            elif item.item_type in _TAX_ITEM_TYPES:
                has_explicit_tax_item = True
                explicit_tax_total += item.amount

        subtotal = _quantize_money(subtotal)
        discount_amount = _quantize_money(proration_credits + extra_discount)
        taxable_base = max(Decimal("0.00"), subtotal - discount_amount)

        if has_explicit_tax_item:
            tax_amount = _quantize_money(explicit_tax_total)
        elif tax_rate is not None and tax_rate > Decimal("0.00"):
            tax_amount = _quantize_money(taxable_base * tax_rate)
            if tax_amount > Decimal("0.00"):
                pct = _quantize_money(tax_rate * Decimal("100"))
                finalized_items.append(
                    InvoiceItemCreate(
                        item_type=InvoiceItemType.TAX,
                        description=f"Tax ({pct}%)",
                        quantity=1,
                        unit_price=tax_amount,
                        amount=tax_amount,
                    )
                )
        else:
            tax_amount = Decimal("0.00")

        total_amount = _quantize_money(taxable_base + tax_amount)
        return subtotal, discount_amount, tax_amount, total_amount, finalized_items

    @staticmethod
    def generate_invoice_for_billing_cycle(
        db: Session,
        billing_cycle_id: int,
        extra_items: Optional[List[InvoiceItemCreate]] = None,
        tax_rate: Optional[Decimal] = None,
        extra_discount: Decimal = Decimal("0.00"),
        due_days: Optional[int] = None,
        notes: Optional[str] = None,
        actor: str = "celery_invoice_worker",
    ) -> Tuple[Invoice, bool]:
        """
        Idempotently generates an Invoice and its InvoiceItems for a BillingCycle
        using PostgreSQL SELECT ... FOR UPDATE row locking and the unique
        billing_cycle_id constraint. Returns (Invoice, created_flag).
        """
        cycle = (
            db.query(BillingCycle)
            .filter(BillingCycle.id == billing_cycle_id)
            .with_for_update()
            .first()
        )
        if cycle is None:
            raise BillingCycleNotFoundError(f"BillingCycle with id {billing_cycle_id} not found.")

        existing = (
            db.query(Invoice)
            .options(selectinload(Invoice.items))
            .filter(Invoice.billing_cycle_id == billing_cycle_id)
            .first()
        )
        if existing is not None:
            return existing, False

        sub = cycle.subscription
        if sub is None:
            raise InvalidInvoiceOperationError(
                f"BillingCycle {billing_cycle_id} has no associated Subscription."
            )

        # Build base subscription charge strictly from subscription's historical pricing snapshot
        plan_label = sub.plan.name if sub.plan else f"Subscription {sub.subscription_code}"
        period_str = (
            f"{ensure_utc(cycle.period_start).date().isoformat()} to "
            f"{ensure_utc(cycle.period_end).date().isoformat()}"
        )
        base_amount = _quantize_money(sub.unit_price * sub.quantity)
        base_item = InvoiceItemCreate(
            item_type=InvoiceItemType.BASE_SUBSCRIPTION,
            description=f"{plan_label} ({sub.billing_interval.value}) - {period_str}",
            quantity=sub.quantity,
            unit_price=sub.unit_price,
            amount=base_amount,
        )

        all_items = [base_item] + list(extra_items or [])
        effective_tax_rate = tax_rate if tax_rate is not None else settings.DEFAULT_TAX_RATE
        subtotal, discount_amount, tax_amount, total_amount, finalized_items = (
            InvoiceService.calculate_totals_from_items(
                items=all_items,
                tax_rate=effective_tax_rate,
                extra_discount=extra_discount,
            )
        )

        issue_dt = datetime.now(timezone.utc)
        effective_due_days = due_days if due_days is not None else settings.DEFAULT_INVOICE_DUE_DAYS
        due_dt = issue_dt + timedelta(days=effective_due_days)
        invoice_number = InvoiceService.generate_invoice_number(db, issue_date=issue_dt)

        invoice = Invoice(
            invoice_number=invoice_number,
            customer_id=sub.customer_id,
            subscription_id=sub.id,
            billing_cycle_id=cycle.id,
            subtotal=subtotal,
            tax_amount=tax_amount,
            discount_amount=discount_amount,
            total_amount=total_amount,
            amount_paid=Decimal("0.00"),
            currency=sub.currency,
            status=InvoiceStatus.OPEN,
            issue_date=issue_dt,
            due_date=due_dt,
            notes=notes,
        )
        for item_data in finalized_items:
            invoice.items.append(
                InvoiceItem(
                    item_type=item_data.item_type,
                    description=item_data.description,
                    quantity=item_data.quantity,
                    unit_price=item_data.unit_price,
                    amount=item_data.amount,
                )
            )

        db.add(invoice)
        try:
            db.flush()
            cycle.status = BillingCycleStatus.COMPLETED
            cycle.processed_at = issue_dt

            db.add(
                AuditLog(
                    entity_type="invoice",
                    entity_id=invoice.id,
                    action="invoice.generated",
                    actor=actor,
                    customer_id=invoice.customer_id,
                    old_values=None,
                    new_values=_serialize_invoice(invoice),
                    description=(
                        f"Generated invoice '{invoice.invoice_number}' for billing_cycle_id={cycle.id} "
                        f"({invoice.total_amount} {invoice.currency})."
                    ),
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            existing_after_conflict = (
                db.query(Invoice)
                .options(selectinload(Invoice.items))
                .filter(Invoice.billing_cycle_id == billing_cycle_id)
                .first()
            )
            if existing_after_conflict is not None:
                return existing_after_conflict, False
            raise

        db.refresh(invoice)
        return invoice, True

    @staticmethod
    def create_itemized_invoice(
        db: Session,
        customer_id: int,
        items: List[InvoiceItemCreate],
        currency: str = "INR",
        subscription_id: Optional[int] = None,
        tax_rate: Optional[Decimal] = None,
        extra_discount: Decimal = Decimal("0.00"),
        due_days: Optional[int] = None,
        notes: Optional[str] = None,
        actor: str = "api",
        commit: bool = True,
    ) -> Invoice:
        """
        Creates a standalone or upgrade/proration/usage invoice from explicit line items.
        If commit=False, flushes and logs audit within the caller's atomic transaction.
        """
        CustomerService.ensure_customer_active(db, customer_id)
        if not items:
            raise InvalidInvoiceOperationError("An invoice must contain at least one line item.")

        effective_tax_rate = tax_rate if tax_rate is not None else settings.DEFAULT_TAX_RATE
        subtotal, discount_amount, tax_amount, total_amount, finalized_items = (
            InvoiceService.calculate_totals_from_items(
                items=items,
                tax_rate=effective_tax_rate,
                extra_discount=extra_discount,
            )
        )

        issue_dt = datetime.now(timezone.utc)
        effective_due_days = due_days if due_days is not None else settings.DEFAULT_INVOICE_DUE_DAYS
        due_dt = issue_dt + timedelta(days=effective_due_days)
        invoice_number = InvoiceService.generate_invoice_number(db, issue_date=issue_dt)

        # If net total is 0.00 (e.g. proration credit >= charge or zero days), mark PAID immediately
        initial_status = InvoiceStatus.PAID if total_amount == Decimal("0.00") else InvoiceStatus.OPEN
        paid_dt = issue_dt if total_amount == Decimal("0.00") else None

        invoice = Invoice(
            invoice_number=invoice_number,
            customer_id=customer_id,
            subscription_id=subscription_id,
            billing_cycle_id=None,
            subtotal=subtotal,
            tax_amount=tax_amount,
            discount_amount=discount_amount,
            total_amount=total_amount,
            amount_paid=Decimal("0.00"),
            currency=currency,
            status=initial_status,
            issue_date=issue_dt,
            due_date=due_dt,
            paid_at=paid_dt,
            notes=notes,
        )
        for item_data in finalized_items:
            invoice.items.append(
                InvoiceItem(
                    item_type=item_data.item_type,
                    description=item_data.description,
                    quantity=item_data.quantity,
                    unit_price=item_data.unit_price,
                    amount=item_data.amount,
                )
            )

        db.add(invoice)
        db.flush()
        db.add(
            AuditLog(
                entity_type="invoice",
                entity_id=invoice.id,
                action="invoice.generated",
                actor=actor,
                customer_id=invoice.customer_id,
                old_values=None,
                new_values=_serialize_invoice(invoice),
                description=f"Generated itemized invoice '{invoice.invoice_number}' ({invoice.total_amount} {invoice.currency}).",
            )
        )
        if commit:
            db.commit()
            db.refresh(invoice)
        else:
            db.flush()
        return invoice

    @staticmethod
    def list_invoices(
        db: Session,
        page: int = 1,
        size: int = 20,
        customer_id: Optional[int] = None,
        subscription_id: Optional[int] = None,
        billing_cycle_id: Optional[int] = None,
        status: Optional[InvoiceStatus] = None,
        invoice_number: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> Tuple[List[Invoice], int]:
        query = (
            db.query(Invoice)
            .options(
                selectinload(Invoice.items),
                joinedload(Invoice.customer),
                joinedload(Invoice.subscription).joinedload(Subscription.plan),
            )
        )
        if customer_id is not None:
            query = query.filter(Invoice.customer_id == customer_id)
        if subscription_id is not None:
            query = query.filter(Invoice.subscription_id == subscription_id)
        if billing_cycle_id is not None:
            query = query.filter(Invoice.billing_cycle_id == billing_cycle_id)
        if status is not None:
            query = query.filter(Invoice.status == status)
        if invoice_number and invoice_number.strip():
            query = query.filter(Invoice.invoice_number.ilike(f"%{invoice_number.strip()}%"))
        if date_from is not None:
            query = query.filter(Invoice.issue_date >= ensure_utc(date_from))
        if date_to is not None:
            query = query.filter(Invoice.issue_date <= ensure_utc(date_to))

        total = query.count()
        items = (
            query.order_by(Invoice.id.desc())
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )
        return items, total

    @staticmethod
    def get_invoice(db: Session, invoice_id: int) -> Invoice:
        invoice = (
            db.query(Invoice)
            .options(
                selectinload(Invoice.items),
                joinedload(Invoice.customer),
                joinedload(Invoice.subscription).joinedload(Subscription.plan),
            )
            .filter(Invoice.id == invoice_id)
            .first()
        )
        if invoice is None:
            raise InvoiceNotFoundError(f"Invoice with id {invoice_id} not found.")
        return invoice

    @staticmethod
    def list_customer_invoices(
        db: Session,
        customer_id: int,
        page: int = 1,
        size: int = 20,
        status: Optional[InvoiceStatus] = None,
        invoice_number: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> Tuple[List[Invoice], int]:
        CustomerService.get_customer(db, customer_id)
        return InvoiceService.list_invoices(
            db=db,
            page=page,
            size=size,
            customer_id=customer_id,
            status=status,
            invoice_number=invoice_number,
            date_from=date_from,
            date_to=date_to,
        )

