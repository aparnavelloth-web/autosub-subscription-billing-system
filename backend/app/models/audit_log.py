from datetime import datetime
from typing import Optional, Any, TYPE_CHECKING
from sqlalchemy import (
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    func,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base

if TYPE_CHECKING:
    from app.models.customer import Customer


class AuditLog(Base):
    """
    Immutable append-only audit log capturing billing lifecycle events,
    subscription state transitions, invoice actions, and administrative mutations.
    Protected by both SQLAlchemy ORM event listeners and PostgreSQL BEFORE UPDATE/DELETE triggers.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity_lookup", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(150), nullable=False, default="system")

    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    old_values: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    # Relationships
    customer: Mapped[Optional["Customer"]] = relationship("Customer")

    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, entity='{self.entity_type}:{self.entity_id}', "
            f"action='{self.action}')>"
        )


@event.listens_for(AuditLog, "before_update")
def _prevent_audit_log_update(mapper, connection, target) -> None:
    """Blocks ORM-level UPDATE operations on immutable AuditLog records."""
    raise PermissionError("AuditLog records are immutable and cannot be modified.")


@event.listens_for(AuditLog, "before_delete")
def _prevent_audit_log_delete(mapper, connection, target) -> None:
    """Blocks ORM-level DELETE operations on immutable AuditLog records."""
    raise PermissionError("AuditLog records are immutable and cannot be deleted.")
