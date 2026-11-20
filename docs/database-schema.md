# Database Schema Documentation

**Project:** Creation of Subscription Billing Automation System Using Recurring Revenue Model  
**Organization:** Infosys Springboard Internship  
**Target DBMS:** PostgreSQL 18  
**ORM & Migrations:** SQLAlchemy 2.0 + Alembic  
**Current Revision:** `47fd1264f12c` (`add_payment_refunds_and_webhook_events`)

---

## 1. Design Overview

The relational schema models a deterministic **Recurring Revenue Billing Engine**. It consists of the **7 core domain entities** required by the Infosys Springboard project specification, plus **1 supporting normalized table** (`invoice_items`):

1. **`plans`** — Subscription tiers, pricing, trial durations, billing intervals, and soft deletion (`deleted_at`).
2. **`customers`** — Subscriber profiles, billing addresses, tax metadata, and soft deletion (`deleted_at`).
3. **`subscriptions`** — Active/trial/past_due/cancelled links between customers and plans with **contracted historical pricing snapshots** (`unit_price`, `currency`, `billing_interval`).
4. **`billing_cycles`** — Individual recurring period execution windows ensuring idempotent Celery Beat billing sweeps.
5. **`invoices`** — Formal billing statements linked 1-to-1 to billing cycles.
6. **`invoice_items`** *(supporting table)* — Normalized itemized line charges per invoice for itemized accounting and ReportLab PDF generation.
7. **`payments`** — Payment transactions, gateway response JSONB payloads, and settlement statuses.
8. **`audit_logs`** — Immutable append-only audit trail protected by both SQLAlchemy ORM listeners and PostgreSQL `BEFORE UPDATE OR DELETE` trigger (`trg_audit_logs_immutable`).

### Key Architectural Safeguards
- **Financial Record Deletion Protection:** All financial foreign keys (`subscriptions.customer_id`, `billing_cycles.subscription_id`, `invoices.customer_id`, `invoices.billing_cycle_id`, `payments.invoice_id`, `payments.customer_id`, `audit_logs.customer_id`) enforce `ON DELETE RESTRICT`. Customers and Plans support non-destructive deactivation via `soft_delete()` (`deleted_at`).
- **Historical Pricing Preservation:** Modifying a `Plan`'s price does not alter existing `Subscription` contracts; `Subscription` snapshots `unit_price`, `currency`, and `billing_interval` automatically upon creation or explicitly via `apply_plan_snapshot()` upon upgrade.
- **Audit Log Immutability:** Enforced in application code (`before_update` / `before_delete`) and PostgreSQL (`trg_audit_logs_immutable`). Controlled maintenance requires setting session-local `app.allow_audit_maintenance = 'on'`.
- **Isolated Test Database:** Integration tests run exclusively against `subscription_billing_test_db` (via `TEST_DATABASE_URL`) and strictly guard against running on `subscription_billing_db`.

> For a visual diagram of tables and cardinalities, see [ER Diagram](er-diagram.md).

---

## 2. PostgreSQL Native Enum Types

| Enum Name | Allowed Values | Entity / Column |
|---|---|---|
| `billing_interval_enum` | `monthly`, `quarterly`, `yearly` | `plans.billing_interval`, `subscriptions.billing_interval` |
| `subscription_status_enum` | `trial`, `active`, `past_due`, `cancelled` | `subscriptions.status` |
| `billing_cycle_status_enum` | `pending`, `processing`, `completed`, `failed` | `billing_cycles.status` |
| `invoice_status_enum` | `draft`, `open`, `paid`, `void`, `uncollectible` | `invoices.status` |
| `invoice_item_type_enum` | `base_subscription`, `plan_upgrade`, `proration_credit`, `proration_debit`, `usage`, `tax` | `invoice_items.item_type` |
| `payment_status_enum` | `pending`, `succeeded`, `failed`, `refunded` | `payments.status` |
| `payment_method_enum` | `credit_card`, `debit_card`, `bank_transfer`, `upi`, `simulated` | `payments.payment_method` |
| `refund_status_enum` | `pending`, `succeeded`, `failed` | `payment_refunds.status` |

---

## 3. Entity Specifications

### 3.1 `plans`
Stores available subscription tiers and pricing configurations.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `name` | `VARCHAR(100)` | NOT NULL, Indexed | Display name (e.g., "Pro Plan") |
| `code` | `VARCHAR(50)` | NOT NULL, UNIQUE (`uq_plans_code`), Indexed | Business plan code (e.g., `PRO-MONTHLY`) |
| `description` | `TEXT` | Nullable | Detailed tier description |
| `price` | `NUMERIC(12, 2)` | NOT NULL, CHECK `price >= 0` | Monetary recurring rate |
| `currency` | `VARCHAR(3)` | NOT NULL, Default `'USD'`, CHECK 3-letter uppercase ISO | ISO 4217 currency code |
| `billing_interval` | `billing_interval_enum` | NOT NULL, Default `'monthly'` | Cadence (`monthly`, `quarterly`, `yearly`) |
| `trial_days` | `INTEGER` | NOT NULL, Default `0`, CHECK `trial_days >= 0` | Free trial window duration |
| `features` | `JSONB` | Nullable, Default `[]` | Feature entitlements JSON array |
| `is_active` | `BOOLEAN` | NOT NULL, Default `true`, Indexed | Controls plan availability |
| `deleted_at` | `TIMESTAMPTZ` | Nullable, Indexed | Soft deletion timestamp |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Last modification timestamp |

---

### 3.2 `customers`
Stores customer contact, organization, and billing identity records.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `customer_code` | `VARCHAR(50)` | NOT NULL, UNIQUE (`uq_customers_customer_code`), Indexed | Public customer ID (`CUST-0001`) |
| `full_name` | `VARCHAR(150)` | NOT NULL, Indexed | Primary contact name |
| `email` | `VARCHAR(255)` | NOT NULL, UNIQUE (`uq_customers_email`), Indexed | Unique billing email address |
| `phone` | `VARCHAR(30)` | Nullable | Contact phone number |
| `company_name` | `VARCHAR(150)` | Nullable | Business organization name |
| `billing_address` | `TEXT` | Nullable | Full mailing/tax address |
| `tax_id` | `VARCHAR(50)` | Nullable | GSTIN / VAT / Tax ID |
| `currency` | `VARCHAR(3)` | NOT NULL, Default `'USD'`, CHECK 3-letter uppercase ISO | Preferred billing currency |
| `is_active` | `BOOLEAN` | NOT NULL, Default `true`, Indexed | Account active status |
| `deleted_at` | `TIMESTAMPTZ` | Nullable, Indexed | Soft deletion timestamp |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Last modification timestamp |

---

### 3.3 `subscriptions`
Links a Customer to a Plan, tracking active period boundaries, trial state, and historical pricing snapshots.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `subscription_code` | `VARCHAR(50)` | NOT NULL, UNIQUE (`uq_subscriptions_subscription_code`), Indexed | Public subscription ID |
| `customer_id` | `INTEGER` | FK `customers.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Subscribing customer |
| `plan_id` | `INTEGER` | FK `plans.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Reference pricing plan |
| `status` | `subscription_status_enum` | NOT NULL, Default `'active'`, Indexed | `trial`, `active`, `past_due`, `cancelled` |
| `quantity` | `INTEGER` | NOT NULL, Default `1`, CHECK `quantity > 0` | Seat/unit multiplier |
| `unit_price` | `NUMERIC(12, 2)` | NOT NULL, CHECK `unit_price >= 0` | Snapshotted price per unit from `Plan` |
| `currency` | `VARCHAR(3)` | NOT NULL, CHECK 3-letter uppercase ISO | Snapshotted currency from `Plan` |
| `billing_interval` | `billing_interval_enum` | NOT NULL | Snapshotted interval from `Plan` |
| `start_date` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Initial activation date |
| `current_period_start` | `TIMESTAMPTZ` | NOT NULL | Current billing window start |
| `current_period_end` | `TIMESTAMPTZ` | NOT NULL, Indexed, CHECK `> current_period_start` | Next billing/renewal boundary |
| `trial_start` | `TIMESTAMPTZ` | Nullable | Trial window start |
| `trial_end` | `TIMESTAMPTZ` | Nullable, CHECK `> trial_start` (when both set) | Trial window expiration |
| `cancel_at_period_end` | `BOOLEAN` | NOT NULL, Default `false` | Auto-cancel at cycle end flag |
| `cancelled_at` | `TIMESTAMPTZ` | Nullable | Cancellation timestamp |
| `is_paused` | `BOOLEAN` | NOT NULL, Default `false`, Indexed | Orthogonal pause state preserving enum contract |
| `paused_at` | `TIMESTAMPTZ` | Nullable | Timestamp when subscription was paused |
| `resumed_at` | `TIMESTAMPTZ` | Nullable | Timestamp when subscription was resumed |
| `paused_remaining_seconds` | `INTEGER` | Nullable, CHECK `>= 0` | Remaining prepaid seconds frozen on pause |
| `pause_reason` | `VARCHAR(255)` | Nullable | Reason for pausing subscription |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Last modification timestamp |

*Composite Indexes:*
- `ix_subscriptions_status_period_end` (`status`, `current_period_end`)
- `ix_subscriptions_billable_lookup` (`status`, `is_paused`, `current_period_end`) optimizes Celery Beat sweeps to skip paused subscriptions.

---

### 3.4 `billing_cycles`
Tracks distinct recurring cycle executions for automated billing idempotency.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `subscription_id` | `INTEGER` | FK `subscriptions.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Parent subscription |
| `cycle_number` | `INTEGER` | NOT NULL, CHECK `cycle_number > 0` | Sequential cycle index (`1, 2, 3...`) |
| `period_start` | `TIMESTAMPTZ` | NOT NULL | Period covered start |
| `period_end` | `TIMESTAMPTZ` | NOT NULL, CHECK `period_end > period_start` | Period covered end |
| `billing_date` | `TIMESTAMPTZ` | NOT NULL, Indexed | Scheduled execution date |
| `status` | `billing_cycle_status_enum` | NOT NULL, Default `'pending'`, Indexed | `pending`, `processing`, `completed`, `failed` |
| `amount` | `NUMERIC(12, 2)` | NOT NULL, CHECK `amount >= 0` | Calculated cycle charge |
| `currency` | `VARCHAR(3)` | NOT NULL, Default `'USD'`, CHECK 3-letter uppercase ISO | Billing currency |
| `processed_at` | `TIMESTAMPTZ` | Nullable | Execution completion timestamp |
| `failure_reason` | `TEXT` | Nullable | Error details if cycle failed |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Last modification timestamp |

*Unique Constraint:* `uq_billing_cycles_subscription_cycle` (`subscription_id`, `cycle_number`) prevents duplicate cycle execution.

---

### 3.5 `invoices`
Formal billing statements linked to customers, subscriptions, and billing cycles.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `invoice_number` | `VARCHAR(50)` | NOT NULL, UNIQUE (`uq_invoices_invoice_number`), Indexed | Sequential invoice ID (`INV-2026-000001` via PostgreSQL `invoice_number_seq`) |
| `customer_id` | `INTEGER` | FK `customers.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Billed customer |
| `subscription_id` | `INTEGER` | FK `subscriptions.id` (`ON DELETE SET NULL`), Nullable, Indexed | Associated subscription |
| `billing_cycle_id` | `INTEGER` | FK `billing_cycles.id` (`ON DELETE RESTRICT`), UNIQUE, Nullable, Indexed | Generating billing cycle (1-to-1 idempotency) |
| `subtotal` | `NUMERIC(12, 2)` | NOT NULL, Default `0.00`, CHECK `subtotal >= 0` | Pre-tax sum of line items |
| `tax_amount` | `NUMERIC(12, 2)` | NOT NULL, Default `0.00`, CHECK `tax_amount >= 0` | Applicable taxes |
| `discount_amount` | `NUMERIC(12, 2)` | NOT NULL, Default `0.00`, CHECK `discount_amount >= 0` | Applied discounts |
| `total_amount` | `NUMERIC(12, 2)` | NOT NULL, CHECK `total_amount >= 0` | Final billed amount |
| `amount_paid` | `NUMERIC(12, 2)` | NOT NULL, Default `0.00`, CHECK `amount_paid >= 0` & `amount_paid <= total_amount` | Historical settled amount applied to invoice |
| `currency` | `VARCHAR(3)` | NOT NULL, Default `'USD'`, CHECK 3-letter uppercase ISO | Invoice currency |
| `status` | `invoice_status_enum` | NOT NULL, Default `'open'`, Indexed | `draft`, `open`, `paid`, `void`, `uncollectible` |
| `issue_date` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Invoice issuance date |
| `due_date` | `TIMESTAMPTZ` | NOT NULL, Indexed, CHECK `due_date >= issue_date` | Payment deadline |
| `paid_at` | `TIMESTAMPTZ` | Nullable | Full payment timestamp |
| `pdf_path` | `VARCHAR(500)` | Nullable | Path to generated ReportLab PDF |
| `notes` | `TEXT` | Nullable | Additional invoice remarks |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Last modification timestamp |

---

### 3.6 `invoice_items` *(Supporting Table)*
**Technical Justification:** Normalizes line-item charges (`base_subscription`, `plan_upgrade`, `proration_credit`, `proration_debit`, `usage`, `tax`) so an invoice reflects multi-component recurring charges, proration adjustments, and taxes cleanly and feeds itemized tables into ReportLab PDF generation.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `invoice_id` | `INTEGER` | FK `invoices.id` (`ON DELETE CASCADE`), NOT NULL, Indexed | Parent invoice |
| `item_type` | `invoice_item_type_enum` | NOT NULL, Default `'base_subscription'`, Indexed | Line item category (`base_subscription`, `plan_upgrade`, `proration_credit`, `proration_debit`, `usage`, `tax`) |
| `description` | `VARCHAR(255)` | NOT NULL | Line item label |
| `quantity` | `INTEGER` | NOT NULL, Default `1`, CHECK `quantity > 0` | Item quantity |
| `unit_price` | `NUMERIC(12, 2)` | NOT NULL, CHECK `unit_price >= 0` | Per-unit monetary rate |
| `amount` | `NUMERIC(12, 2)` | NOT NULL, CHECK `amount >= 0` | Total line amount |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |

---

### 3.7 `payments`
Records payment transaction attempts and settlement statuses against invoices.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `transaction_reference` | `VARCHAR(100)` | NOT NULL, UNIQUE (`uq_payments_transaction_reference`), Indexed | Unique payment reference |
| `idempotency_key` | `VARCHAR(100)` | Nullable, UNIQUE (`uq_payments_idempotency_key`), Indexed | Client idempotency token preventing duplicate charges |
| `invoice_id` | `INTEGER` | FK `invoices.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Target invoice |
| `customer_id` | `INTEGER` | FK `customers.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Paying customer |
| `amount` | `NUMERIC(12, 2)` | NOT NULL, CHECK `amount > 0` | Payment transaction amount |
| `refunded_amount` | `NUMERIC(12, 2)` | NOT NULL, Default `0.00`, CHECK `0 <= refunded_amount <= amount` | Cumulative refunded total against this payment |
| `currency` | `VARCHAR(3)` | NOT NULL, Default `'USD'`, CHECK 3-letter uppercase ISO | Currency |
| `payment_method` | `payment_method_enum` | NOT NULL, Default `'simulated'` | `credit_card`, `debit_card`, `bank_transfer`, `upi`, `simulated` |
| `status` | `payment_status_enum` | NOT NULL, Default `'pending'`, Indexed | `pending`, `succeeded`, `failed`, `refunded` |
| `payment_date` | `TIMESTAMPTZ` | Nullable | Settlement timestamp |
| `failure_reason` | `TEXT` | Nullable | Gateway decline details |
| `gateway_response` | `JSONB` | Nullable | Raw payment processor response |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Last modification timestamp |

---

### 3.8 `payment_refunds` *(Supporting Table)*
Immutable ledger tracking partial and full refunds without mutating historical `invoices.amount_paid`.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `refund_reference` | `VARCHAR(100)` | NOT NULL, UNIQUE (`uq_payment_refunds_refund_reference`), Indexed | Unique refund transaction reference |
| `idempotency_key` | `VARCHAR(100)` | Nullable, UNIQUE (`uq_payment_refunds_idempotency_key`), Indexed | Client idempotency token preventing double refunds |
| `payment_id` | `INTEGER` | FK `payments.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Source payment |
| `invoice_id` | `INTEGER` | FK `invoices.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Associated invoice |
| `customer_id` | `INTEGER` | FK `customers.id` (`ON DELETE RESTRICT`), NOT NULL, Indexed | Associated customer |
| `amount` | `NUMERIC(12, 2)` | NOT NULL, CHECK `amount > 0` | Refunded amount for this refund entry |
| `currency` | `VARCHAR(3)` | NOT NULL, Default `'USD'`, CHECK 3-letter uppercase ISO | Currency |
| `status` | `refund_status_enum` | NOT NULL, Default `'pending'`, Indexed | `pending`, `succeeded`, `failed` |
| `reason` | `TEXT` | Nullable | Refund reason / note |
| `gateway_response` | `JSONB` | Nullable | Gateway refund response payload |
| `refunded_at` | `TIMESTAMPTZ` | Nullable | Refund completion timestamp |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Last modification timestamp |

---

### 3.9 `webhook_events` *(Supporting Table)*
Stores verified payment gateway webhook deliveries and guarantees idempotent replay protection.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `event_id` | `VARCHAR(100)` | NOT NULL, UNIQUE (`uq_webhook_events_event_id`), Indexed | Unique gateway event identifier |
| `gateway` | `VARCHAR(50)` | NOT NULL, Default `'mock_gateway'` | Webhook origin identifier |
| `event_type` | `VARCHAR(100)` | NOT NULL, Indexed | Gateway event type (`payment.succeeded`, etc.) |
| `payment_id` | `INTEGER` | FK `payments.id` (`ON DELETE RESTRICT`), Nullable, Indexed | Resolved payment target |
| `transaction_reference` | `VARCHAR(100)` | Nullable, Indexed | Gateway transaction reference in payload |
| `signature_verified` | `BOOLEAN` | NOT NULL, Default `false` | Whether HMAC signature check passed |
| `processing_status` | `VARCHAR(30)` | NOT NULL, Default `'processed'`, Indexed, CHECK IN (`received`, `processed`, `ignored_stale`, `rejected`) | Webhook processing outcome |
| `payload` | `JSONB` | NOT NULL | Raw verified webhook event payload |
| `error_message` | `TEXT` | Nullable | Diagnostic message on rejection/error |
| `received_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()` | Receipt timestamp |
| `processed_at` | `TIMESTAMPTZ` | Nullable | Processing completion timestamp |

---

### 3.8 `audit_logs`
Immutable append-only history of billing system events and entity state transitions.

| Column | PostgreSQL Type | Constraints / Defaults | Description |
|---|---|---|---|
| `id` | `INTEGER` | PK, Autoincrement, Indexed | Surrogate primary key |
| `entity_type` | `VARCHAR(50)` | NOT NULL, Indexed | Target entity (`subscription`, `invoice`, etc.) |
| `entity_id` | `INTEGER` | NOT NULL, Indexed | Primary key of mutated entity |
| `action` | `VARCHAR(100)` | NOT NULL, Indexed | Event verb (`subscription.cancelled`, `invoice.paid`) |
| `actor` | `VARCHAR(150)` | NOT NULL, Default `'system'` | Originator (`system`, `celery_beat`, user email) |
| `customer_id` | `INTEGER` | FK `customers.id` (`ON DELETE RESTRICT`), Nullable, Indexed | Associated customer (if applicable) |
| `old_values` | `JSONB` | Nullable | Snapshot prior to mutation |
| `new_values` | `JSONB` | Nullable | Snapshot after mutation |
| `ip_address` | `VARCHAR(45)` | Nullable | Client IP address (IPv4/IPv6) |
| `description` | `TEXT` | Nullable | Human-readable event summary |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, Default `now()`, Indexed | Event occurrence timestamp |

*Composite Index:* `ix_audit_logs_entity_lookup` (`entity_type`, `entity_id`) speeds up per-record audit trail filtering.  
*Database Trigger:* `trg_audit_logs_immutable` executes `prevent_audit_log_mutation()` before `UPDATE` or `DELETE` on each row.

---

## 4. Database Migrations (Alembic)

Alembic is configured under `backend/alembic/` and loads the database connection URI dynamically from `.env` (`settings.sync_database_url` for development, `settings.test_database_url` for isolated tests) with zero hardcoded passwords.

```powershell
# Check current database revision
python -m alembic current

# Apply all pending migrations
python -m alembic upgrade head

# Check schema drift
python -m alembic check
```
