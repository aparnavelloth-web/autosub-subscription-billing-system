import uuid
from typing import Any, Dict, List, Tuple
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.subscription import Subscription
from app.schemas.customer import CustomerCreate, CustomerUpdate


class CustomerNotFoundError(Exception):
    """Raised when a requested customer ID does not exist."""


class DuplicateCustomerError(Exception):
    """Raised when email or customer_code conflicts with an existing customer."""


class CustomerInactiveOrDeletedError(Exception):
    """Raised when attempting restricted operations on a soft-deleted or inactive customer."""


def _generate_customer_code() -> str:
    return f"CUST-{uuid.uuid4().hex[:8].upper()}"


def _serialize_customer(customer: Customer) -> Dict[str, Any]:
    return {
        "id": customer.id,
        "customer_code": customer.customer_code,
        "full_name": customer.full_name,
        "email": customer.email,
        "phone": customer.phone,
        "company_name": customer.company_name,
        "billing_address": customer.billing_address,
        "tax_id": customer.tax_id,
        "currency": customer.currency,
        "is_active": customer.is_active,
        "deleted_at": customer.deleted_at.isoformat() if customer.deleted_at else None,
    }


class CustomerService:
    """
    Domain service encapsulating customer lifecycle operations,
    uniqueness constraints, soft-deletion guards, and audit logging.
    """

    @staticmethod
    def create_customer(db: Session, payload: CustomerCreate, actor: str = "api") -> Customer:
        existing_email = (
            db.query(Customer)
            .filter(func.lower(Customer.email) == payload.email.lower())
            .first()
        )
        if existing_email is not None:
            raise DuplicateCustomerError(f"A customer with email '{payload.email}' already exists.")

        code = payload.customer_code or _generate_customer_code()
        existing_code = db.query(Customer).filter(Customer.customer_code == code).first()
        if existing_code is not None:
            raise DuplicateCustomerError(f"A customer with code '{code}' already exists.")

        data = payload.model_dump()
        data["customer_code"] = code
        customer = Customer(**data)
        db.add(customer)
        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="customer",
                    entity_id=customer.id,
                    action="customer.created",
                    actor=actor,
                    customer_id=customer.id,
                    old_values=None,
                    new_values=_serialize_customer(customer),
                    description=f"Created customer '{customer.customer_code}'.",
                )
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateCustomerError("Customer email or customer code already exists.") from exc

        db.refresh(customer)
        return customer

    @staticmethod
    def list_customers(
        db: Session,
        page: int = 1,
        size: int = 20,
        include_deleted: bool = False,
        active_only: bool = True,
    ) -> Tuple[List[Customer], int]:
        query = db.query(Customer)
        if not include_deleted:
            query = query.filter(Customer.deleted_at.is_(None))
            if active_only:
                query = query.filter(Customer.is_active.is_(True))

        total = query.count()
        items = (
            query.order_by(Customer.id.asc())
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )
        return items, total

    @staticmethod
    def get_customer(db: Session, customer_id: int) -> Customer:
        customer = db.get(Customer, customer_id)
        if customer is None:
            raise CustomerNotFoundError(f"Customer with id {customer_id} not found.")
        return customer

    @staticmethod
    def ensure_customer_active(db: Session, customer_id: int) -> Customer:
        customer = CustomerService.get_customer(db, customer_id)
        if customer.is_deleted or not customer.is_active:
            raise CustomerInactiveOrDeletedError(
                f"Customer '{customer.customer_code}' (id={customer.id}) is inactive or deleted."
            )
        return customer

    @staticmethod
    def update_customer(
        db: Session,
        customer_id: int,
        payload: CustomerUpdate,
        actor: str = "api",
    ) -> Customer:
        customer = CustomerService.get_customer(db, customer_id)
        if customer.is_deleted:
            raise CustomerInactiveOrDeletedError(
                f"Customer '{customer.customer_code}' (id={customer.id}) is soft-deleted and cannot be updated."
            )

        update_data = payload.model_dump(exclude_unset=True)
        if "email" in update_data and update_data["email"].lower() != customer.email.lower():
            duplicate = (
                db.query(Customer)
                .filter(func.lower(Customer.email) == update_data["email"].lower())
                .first()
            )
            if duplicate is not None:
                raise DuplicateCustomerError(
                    f"A customer with email '{update_data['email']}' already exists."
                )

        old_values = _serialize_customer(customer)
        for field, value in update_data.items():
            setattr(customer, field, value)

        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="customer",
                    entity_id=customer.id,
                    action="customer.updated",
                    actor=actor,
                    customer_id=customer.id,
                    old_values=old_values,
                    new_values=_serialize_customer(customer),
                    description=f"Updated customer '{customer.customer_code}'.",
                )
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateCustomerError("Customer email conflicts with an existing customer.") from exc

        db.refresh(customer)
        return customer

    @staticmethod
    def list_customer_subscriptions(
        db: Session,
        customer_id: int,
        page: int = 1,
        size: int = 20,
    ) -> Tuple[List[Subscription], int]:
        CustomerService.get_customer(db, customer_id)
        query = db.query(Subscription).filter(Subscription.customer_id == customer_id)
        total = query.count()
        items = (
            query.order_by(Subscription.id.desc())
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )
        return items, total

