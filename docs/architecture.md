# System Architecture & Technical Specifications

**Project Title:** Creation of Subscription Billing Automation System Using Recurring Revenue Model  
**Internship:** Infosys Springboard  
**Phase:** Initial Foundation (Phase 1)

---

## 1. Architectural Overview

The **Subscription Billing Automation System** is engineered following clean architecture and modular software engineering principles. The system decouples the presentation layer (React SPA), API orchestration layer (FastAPI), asynchronous job scheduling layer (Celery & Celery Beat), and relational persistence layer (PostgreSQL).

```
+-------------------------------------------------------------+
|                     React.js Frontend                       |
|               (Vite, JavaScript, CSS System)                |
+------------------------------+------------------------------+
                               |  HTTP REST / CORS
                               v
+-------------------------------------------------------------+
|                   FastAPI Backend Gateway                   |
|   - CORS Middleware      - Pydantic Settings & Validation   |
|   - System Health API    - Structured Routers (/api/v1)     |
+------------------------------+------------------------------+
                               |
            +------------------+------------------+
            |                                     |
            v                                     v
+-----------------------+             +-----------------------+
|  SQLAlchemy 2.0 ORM   |             |   Celery / Celery Beat|
|  - Engine & Sessions  |             |   - Recurring Schedules|
|  - Declarative Models |             |   - Dunning Workflows |
+-----------+-----------+             +-----------+-----------+
            |                                     |
            +------------------+------------------+
                               |
                               v
+-------------------------------------------------------------+
|                    PostgreSQL Database                      |
|                (Dockerized or Self-Hosted)                  |
+-------------------------------------------------------------+
```

---

## 2. Directory Layout & Module Responsibilities

```text
subscription-billing-system/
│
├── backend/                        # Backend service root
│   ├── app/                        # Application core package
│   │   ├── api/                    # API endpoints & routing
│   │   │   ├── v1/
│   │   │   │   ├── endpoints/      # Domain specific endpoint handlers
│   │   │   │   │   ├── health.py   # Health check & database probe API
│   │   │   │   │   └── __init__.py
│   │   │   │   ├── router.py       # Aggregates v1 sub-routers
│   │   │   │   └── __init__.py
│   │   │   └── __init__.py
│   │   ├── core/                   # Application-wide configurations
│   │   │   ├── config.py           # Pydantic Settings with .env loading
│   │   │   └── __init__.py
│   │   ├── database/               # Database connectivity & session management
│   │   │   ├── base.py             # SQLAlchemy DeclarativeBase
│   │   │   ├── session.py          # Engine, sessionmaker, get_db dependency
│   │   │   └── __init__.py
│   │   ├── models/                 # SQLAlchemy entity declarations
│   │   │   └── __init__.py
│   │   ├── schemas/                # Pydantic validation schemas
│   │   │   ├── health.py           # System & database health models
│   │   │   └── __init__.py
│   │   ├── services/               # Business logic, billing cycles & gateways
│   │   │   └── __init__.py
│   │   ├── main.py                 # FastAPI application factory, CORS, entrypoint
│   │   └── __init__.py
│   ├── tests/                      # Automated test suite
│   │   ├── conftest.py             # Test fixtures & test DB setup
│   │   ├── test_health.py          # Health check & configuration tests
│   │   └── __init__.py
│   ├── .env                        # Local development environment configuration
│   ├── .env.example                # Template for environment configuration
│   ├── pytest.ini                  # Pytest test runner configuration
│   └── requirements.txt            # Python backend dependencies
│
├── frontend/                       # React.js Single Page Application
│   ├── src/
│   │   ├── assets/                 # Static visual assets
│   │   ├── components/             # Reusable UI components
│   │   │   ├── Navbar.jsx          # Header with branding and external documentation links
│   │   │   ├── Hero.jsx            # Project intro, badges, and CTA
│   │   │   ├── HealthStatus.jsx    # Live health-check telemetry polling
│   │   │   ├── Features.jsx        # Core billing capability cards
│   │   │   ├── ArchitecturePreview.jsx # 4-step recurring workflow overview
│   │   │   └── Footer.jsx          # Attribution and tech stack summary
│   │   ├── App.jsx                 # Main layout component
│   │   ├── App.css                 # Component styling and layout grids
│   │   ├── index.css               # Design tokens, variables, typography
│   │   └── main.jsx                # React DOM entrypoint
│   ├── index.html                  # HTML template
│   ├── package.json                # Frontend package dependencies & scripts
│   └── vite.config.js              # Vite server & reverse proxy configuration
│
├── docs/                           # Project documentation & specs
│   └── architecture.md             # Architecture overview and design documentation
│
├── docker-compose.yml              # PostgreSQL container configuration
├── .env.example                    # Global environment variable template
├── .gitignore                      # Git exclusion rules
└── README.md                       # Comprehensive setup and usage instructions
```

---

## 3. Technology Stack & Role Matrix

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| **Backend Framework** | FastAPI | >=0.115.0 | Async RESTful API layer, OpenAPI documentation, dependency injection |
| **Data Validation** | Pydantic / Pydantic Settings | >=2.10.0 | Environment configuration parsing, request/response validation |
| **ORM** | SQLAlchemy | >=2.0.36 | Database abstraction, declarative modeling, session lifecycle |
| **Database** | PostgreSQL | 16 (Alpine) | Persistent relational store for plans, subscriptions, and invoices |
| **Frontend Framework** | React.js | 19.x | Component-based interactive UI |
| **Build Tool** | Vite | 5.x | High-performance bundling, development server, API proxy |
| **Containerization** | Docker Compose | 3.8 Spec | Isolated, reproducible PostgreSQL database environment |

---

## 4. Recurring Revenue Billing Model Plan

Future modules will integrate:
1. **Plan & Subscription Lifecycle Management**:
   - Monthly, quarterly, and annual billing cadences.
   - Tiered seat pricing and usage limits.
2. **Automated Billing Engine**:
   - Celery Beat cron scheduling for due invoice generation.
   - Dunning management (grace period, automatic retries).
3. **Invoice Generation**:
   - ReportLab automated PDF invoice generation with downloadable receipts.
4. **Analytics Dashboard**:
   - Recharts visual charts for MRR, ARR, churn rate, and active subscriber metrics.

