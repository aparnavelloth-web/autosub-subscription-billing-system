"""harden_financial_constraints_and_pricing_snapshot

Revision ID: 67a52ca5835a
Revises: fa8c8746303f
Create Date: 2026-11-10 21:00:02.928755

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '67a52ca5835a'
down_revision: Union[str, Sequence[str], None] = 'fa8c8746303f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema safely while preserving existing records."""
    # 1. Soft deletion columns on plans & customers
    op.add_column('plans', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_plans_deleted_at'), 'plans', ['deleted_at'], unique=False)

    op.add_column('customers', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_customers_deleted_at'), 'customers', ['deleted_at'], unique=False)

    # 2. Historical pricing snapshot columns on subscriptions (nullable -> backfill -> NOT NULL)
    billing_interval_enum = postgresql.ENUM(
        'monthly', 'quarterly', 'yearly',
        name='billing_interval_enum',
        create_type=False,
    )
    op.add_column('subscriptions', sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('subscriptions', sa.Column('currency', sa.String(length=3), nullable=True))
    op.add_column('subscriptions', sa.Column('billing_interval', billing_interval_enum, nullable=True))

    # Safe data backfill for any existing subscriptions
    op.execute(
        """
        UPDATE subscriptions AS s
        SET unit_price = p.price,
            currency = p.currency,
            billing_interval = p.billing_interval
        FROM plans AS p
        WHERE s.plan_id = p.id
          AND (s.unit_price IS NULL OR s.currency IS NULL OR s.billing_interval IS NULL);
        """
    )

    op.alter_column('subscriptions', 'unit_price', existing_type=sa.Numeric(precision=12, scale=2), nullable=False)
    op.alter_column('subscriptions', 'currency', existing_type=sa.String(length=3), nullable=False)
    op.alter_column('subscriptions', 'billing_interval', existing_type=billing_interval_enum, nullable=False)

    # 3. Replace unsafe ON DELETE CASCADE / SET NULL on financial & audit FKs with RESTRICT
    op.drop_constraint('audit_logs_customer_id_fkey', 'audit_logs', type_='foreignkey')
    op.create_foreign_key(
        'audit_logs_customer_id_fkey', 'audit_logs', 'customers',
        ['customer_id'], ['id'], ondelete='RESTRICT',
    )

    op.drop_constraint('subscriptions_customer_id_fkey', 'subscriptions', type_='foreignkey')
    op.create_foreign_key(
        'subscriptions_customer_id_fkey', 'subscriptions', 'customers',
        ['customer_id'], ['id'], ondelete='RESTRICT',
    )

    op.drop_constraint('billing_cycles_subscription_id_fkey', 'billing_cycles', type_='foreignkey')
    op.create_foreign_key(
        'billing_cycles_subscription_id_fkey', 'billing_cycles', 'subscriptions',
        ['subscription_id'], ['id'], ondelete='RESTRICT',
    )

    op.drop_constraint('invoices_customer_id_fkey', 'invoices', type_='foreignkey')
    op.create_foreign_key(
        'invoices_customer_id_fkey', 'invoices', 'customers',
        ['customer_id'], ['id'], ondelete='RESTRICT',
    )

    op.drop_constraint('invoices_billing_cycle_id_fkey', 'invoices', type_='foreignkey')
    op.create_foreign_key(
        'invoices_billing_cycle_id_fkey', 'invoices', 'billing_cycles',
        ['billing_cycle_id'], ['id'], ondelete='RESTRICT',
    )

    op.drop_constraint('payments_invoice_id_fkey', 'payments', type_='foreignkey')
    op.create_foreign_key(
        'payments_invoice_id_fkey', 'payments', 'invoices',
        ['invoice_id'], ['id'], ondelete='RESTRICT',
    )

    op.drop_constraint('payments_customer_id_fkey', 'payments', type_='foreignkey')
    op.create_foreign_key(
        'payments_customer_id_fkey', 'payments', 'customers',
        ['customer_id'], ['id'], ondelete='RESTRICT',
    )

    # 4. Additional financial, boundary, and currency CheckConstraints
    op.create_check_constraint(
        'ck_plans_currency_iso', 'plans',
        'char_length(currency) = 3 AND currency = UPPER(currency)',
    )
    op.create_check_constraint(
        'ck_customers_currency_iso', 'customers',
        'char_length(currency) = 3 AND currency = UPPER(currency)',
    )
    op.create_check_constraint(
        'ck_subscriptions_unit_price_non_negative', 'subscriptions',
        'unit_price >= 0',
    )
    op.create_check_constraint(
        'ck_subscriptions_period_valid', 'subscriptions',
        'current_period_end > current_period_start',
    )
    op.create_check_constraint(
        'ck_subscriptions_trial_valid', 'subscriptions',
        'trial_end IS NULL OR trial_start IS NULL OR trial_end > trial_start',
    )
    op.create_check_constraint(
        'ck_subscriptions_currency_iso', 'subscriptions',
        'char_length(currency) = 3 AND currency = UPPER(currency)',
    )
    op.create_check_constraint(
        'ck_billing_cycles_period_valid', 'billing_cycles',
        'period_end > period_start',
    )
    op.create_check_constraint(
        'ck_billing_cycles_currency_iso', 'billing_cycles',
        'char_length(currency) = 3 AND currency = UPPER(currency)',
    )
    op.create_check_constraint(
        'ck_invoices_subtotal_non_negative', 'invoices',
        'subtotal >= 0',
    )
    op.create_check_constraint(
        'ck_invoices_tax_amount_non_negative', 'invoices',
        'tax_amount >= 0',
    )
    op.create_check_constraint(
        'ck_invoices_discount_amount_non_negative', 'invoices',
        'discount_amount >= 0',
    )
    op.create_check_constraint(
        'ck_invoices_due_date_valid', 'invoices',
        'due_date >= issue_date',
    )
    op.create_check_constraint(
        'ck_invoices_currency_iso', 'invoices',
        'char_length(currency) = 3 AND currency = UPPER(currency)',
    )
    op.create_check_constraint(
        'ck_invoice_items_unit_price_non_negative', 'invoice_items',
        'unit_price >= 0',
    )
    op.create_check_constraint(
        'ck_invoice_items_amount_non_negative', 'invoice_items',
        'amount >= 0',
    )
    op.create_check_constraint(
        'ck_payments_currency_iso', 'payments',
        'char_length(currency) = 3 AND currency = UPPER(currency)',
    )

    # 5. PostgreSQL database-level immutability trigger on audit_logs
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            IF current_setting('app.allow_audit_maintenance', true) = 'on' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Audit logs are immutable: % operations on audit_logs are prohibited.', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_mutation();
        """
    )


def downgrade() -> None:
    """Downgrade schema changes from this revision."""
    # Remove audit_logs immutability trigger & function
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation();")

    # Drop added check constraints
    op.drop_constraint('ck_payments_currency_iso', 'payments', type_='check')
    op.drop_constraint('ck_invoice_items_amount_non_negative', 'invoice_items', type_='check')
    op.drop_constraint('ck_invoice_items_unit_price_non_negative', 'invoice_items', type_='check')
    op.drop_constraint('ck_invoices_currency_iso', 'invoices', type_='check')
    op.drop_constraint('ck_invoices_due_date_valid', 'invoices', type_='check')
    op.drop_constraint('ck_invoices_discount_amount_non_negative', 'invoices', type_='check')
    op.drop_constraint('ck_invoices_tax_amount_non_negative', 'invoices', type_='check')
    op.drop_constraint('ck_invoices_subtotal_non_negative', 'invoices', type_='check')
    op.drop_constraint('ck_billing_cycles_currency_iso', 'billing_cycles', type_='check')
    op.drop_constraint('ck_billing_cycles_period_valid', 'billing_cycles', type_='check')
    op.drop_constraint('ck_subscriptions_currency_iso', 'subscriptions', type_='check')
    op.drop_constraint('ck_subscriptions_trial_valid', 'subscriptions', type_='check')
    op.drop_constraint('ck_subscriptions_period_valid', 'subscriptions', type_='check')
    op.drop_constraint('ck_subscriptions_unit_price_non_negative', 'subscriptions', type_='check')
    op.drop_constraint('ck_customers_currency_iso', 'customers', type_='check')
    op.drop_constraint('ck_plans_currency_iso', 'plans', type_='check')

    # Restore previous foreign keys
    op.drop_constraint('payments_customer_id_fkey', 'payments', type_='foreignkey')
    op.create_foreign_key(
        'payments_customer_id_fkey', 'payments', 'customers',
        ['customer_id'], ['id'], ondelete='CASCADE',
    )

    op.drop_constraint('payments_invoice_id_fkey', 'payments', type_='foreignkey')
    op.create_foreign_key(
        'payments_invoice_id_fkey', 'payments', 'invoices',
        ['invoice_id'], ['id'], ondelete='CASCADE',
    )

    op.drop_constraint('invoices_billing_cycle_id_fkey', 'invoices', type_='foreignkey')
    op.create_foreign_key(
        'invoices_billing_cycle_id_fkey', 'invoices', 'billing_cycles',
        ['billing_cycle_id'], ['id'], ondelete='SET NULL',
    )

    op.drop_constraint('invoices_customer_id_fkey', 'invoices', type_='foreignkey')
    op.create_foreign_key(
        'invoices_customer_id_fkey', 'invoices', 'customers',
        ['customer_id'], ['id'], ondelete='CASCADE',
    )

    op.drop_constraint('billing_cycles_subscription_id_fkey', 'billing_cycles', type_='foreignkey')
    op.create_foreign_key(
        'billing_cycles_subscription_id_fkey', 'billing_cycles', 'subscriptions',
        ['subscription_id'], ['id'], ondelete='CASCADE',
    )

    op.drop_constraint('subscriptions_customer_id_fkey', 'subscriptions', type_='foreignkey')
    op.create_foreign_key(
        'subscriptions_customer_id_fkey', 'subscriptions', 'customers',
        ['customer_id'], ['id'], ondelete='CASCADE',
    )

    op.drop_constraint('audit_logs_customer_id_fkey', 'audit_logs', type_='foreignkey')
    op.create_foreign_key(
        'audit_logs_customer_id_fkey', 'audit_logs', 'customers',
        ['customer_id'], ['id'], ondelete='SET NULL',
    )

    # Drop historical pricing snapshot columns
    op.drop_column('subscriptions', 'billing_interval')
    op.drop_column('subscriptions', 'currency')
    op.drop_column('subscriptions', 'unit_price')

    # Drop soft deletion columns
    op.drop_index(op.f('ix_customers_deleted_at'), table_name='customers')
    op.drop_column('customers', 'deleted_at')

    op.drop_index(op.f('ix_plans_deleted_at'), table_name='plans')
    op.drop_column('plans', 'deleted_at')
