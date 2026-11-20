# Subscription Billing Automation System Using Recurring Revenue Model

**Infosys Springboard Internship Project**  
*Phase 1: Project Foundation & Baseline Architecture*

---

## 📌 Project Overview

The **Subscription Billing Automation System** is an enterprise-grade web application designed to automate the lifecycle of recurring subscriptions, customer billing schedules, invoice creation, and SaaS financial analytics.

This repository contains the clean foundation containing:
- **FastAPI** backend with asynchronous request handling and OpenAPI interactive documentation.
- **SQLAlchemy 2.0** ORM layer with modular database session management and dependency injection.
- **PostgreSQL** database configuration using environment variables and Docker Compose.
- **React.js + Vite** frontend configured with clean JavaScript and CSS styling, featuring a modern landing page with live backend health telemetry.
- **CORS** integration allowing seamless decoupled frontend-backend communication.
- **Unit test suite** validating configurations, route registration, and health check endpoints.

---

## 🗂 Project Directory Structure

```text
subscription-billing-system/
│
├── backend/                        # Backend Application Service
│   ├── app/
│   │   ├── api/                    # API Routing Layer
│   │   │   ├── v1/
│   │   │   │   ├── endpoints/      # Endpoint controllers
│   │   │   │   │   ├── health.py   # System & Database health check endpoint
│   │   │   │   │   └── __init__.py
│   │   │   │   ├── router.py       # Main v1 API router aggregation
│   │   │   │   └── __init__.py
│   │   │   └── __init__.py
│   │   ├── core/                   # Application Core Configurations
│   │   │   ├── config.py           # Pydantic Settings reading .env variables
│   │   │   └── __init__.py
│   │   ├── database/               # Database Connection & Sessions
│   │   │   ├── base.py             # SQLAlchemy DeclarativeBase model foundation
│   │   │   ├── session.py          # Engine creation, SessionLocal, get_db dependency
│   │   │   └── __init__.py
│   │   ├── models/                 # SQLAlchemy domain models (for future phases)
│   │   │   └── __init__.py
│   │   ├── schemas/                # Pydantic validation schemas
│   │   │   ├── health.py           # HealthCheckResponse & DatabaseHealth schemas
│   │   │   └── __init__.py
│   │   ├── services/               # Business logic services (for future phases)
│   │   │   └── __init__.py
│   │   ├── main.py                 # FastAPI app entrypoint with CORS & routes
│   │   └── __init__.py
│   ├── tests/                      # Automated Test Suite
│   │   ├── conftest.py             # Test fixtures & testing configurations
│   │   ├── test_health.py          # Unit tests for settings, routes, and healthcheck
│   │   └── __init__.py
│   ├── .env                        # Local development environment file
│   ├── .env.example                # Environment variables template
│   ├── pytest.ini                  # Pytest configuration
│   └── requirements.txt            # Python dependencies specification
│
├── frontend/                       # React.js Frontend (Vite)
│   ├── src/
│   │   ├── assets/                 # Static media and assets
│   │   ├── components/             # Reusable UI components
│   │   │   ├── Navbar.jsx          # Top navigation with live docs links
│   │   │   ├── Hero.jsx            # Project headline, internship badge, and CTAs
│   │   │   ├── HealthStatus.jsx    # Real-time backend & database telemetry card
│   │   │   ├── Features.jsx        # Recurring billing feature highlights
│   │   │   ├── ArchitecturePreview.jsx # 4-stage automated billing pipeline
│   │   │   └── Footer.jsx          # Project attribution & stack footer
│   │   ├── App.jsx                 # Main React layout
│   │   ├── App.css                 # Component layout styling
│   │   ├── index.css               # Design tokens, variables, and typography
│   │   └── main.jsx                # React root entrypoint
│   ├── index.html                  # HTML entrypoint
│   ├── package.json                # Frontend dependencies and npm scripts
│   └── vite.config.js              # Vite server & reverse proxy configuration
│
├── docs/                           # Documentation
│   └── architecture.md             # Detailed architecture specification
│
├── docker-compose.yml              # PostgreSQL container configuration
├── .env.example                    # Root environment configuration template
├── .gitignore                      # Git ignored files & patterns
└── README.md                       # Project documentation & setup instructions
```

---

## 🛠 Technology Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.0, PostgreSQL, Pydantic v2, Uvicorn
- **Frontend:** React.js, JavaScript, CSS3, Vite
- **Infrastructure / Containers:** Docker Compose (PostgreSQL 16 Alpine)
- **Testing:** Pytest

---

## 🚀 Step-by-Step Setup Instructions

### 1. Prerequisites
- **Python:** Version 3.10 or higher
- **Node.js:** Version 18 or higher (with `npm`)
- **Docker & Docker Compose:** For running PostgreSQL locally

---

### 2. Database Setup (PostgreSQL with Docker Compose)

To start the isolated PostgreSQL database:

```bash
# Navigate to the project root
cd subscription-billing-system

# Start PostgreSQL in detached mode
docker compose up -d
```

Verify that the container is healthy:
```bash
docker compose ps
```

*Note: Environment variables for database credentials can be configured in `.env` based on `.env.example`.*

---

### 3. Backend Setup & Execution (Independent)

The backend can run completely independently.

```bash
# Navigate to backend directory
cd subscription-billing-system/backend

# Create a virtual environment (if not already created)
python -m venv .venv

# Activate virtual environment
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Linux / macOS:
source .venv/bin/activate

# Install required dependencies
pip install -r requirements.txt

# Create .env from template (if needed)
cp .env.example .env

# Run the FastAPI server with auto-reload
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Once started:
- **API Base URL:** [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs:** [http://localhost:8000/api/v1/docs](http://localhost:8000/api/v1/docs)
- **Health Check Endpoint:** [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

---

### 4. Running Backend Tests

Run the test suite using `pytest`:

```bash
cd subscription-billing-system/backend
pytest -v
```

All 5 baseline foundation tests will execute and validate configuration, route availability, and health schemas.

---

### 5. Frontend Setup & Execution (Independent)

The frontend is a Vite + React application that can run independently.

```bash
# Navigate to frontend directory
cd subscription-billing-system/frontend

# Install dependencies (if setting up on a new machine)
npm install

# Start the local development server
npm run dev
```

The frontend will start at:
- **Local URL:** [http://localhost:5173](http://localhost:5173)

The landing page features a **live backend telemetry card** that periodically queries `/api/v1/health` and displays the operational state of the FastAPI gateway and database connection.

---

## 🔒 Security Best Practices Implemented

1. **No Hardcoded Passwords:** All database credentials, ports, and hosts are loaded dynamically from environment variables using `pydantic-settings`.
2. **CORS Whitelisting:** Configured to explicitly permit origins (`http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:3000`).
3. **Database Pre-Ping:** SQLAlchemy engine uses `pool_pre_ping=True` to detect and recover from dropped or restarting database connections.
4. **Resilient Health Check:** The health check endpoint (`/api/v1/health`) executes non-blocking connection verification (`SELECT 1`), returning clear telemetry without crashing the API process when the database container is offline.

---

## 📋 Next Phases Roadmap

- **Phase 2:** Database schema design & migrations (Customers, Subscription Plans, Subscriptions, Invoices, Payment Transactions).
- **Phase 3:** Subscription lifecycle API (Plan creation, subscription upgrade/downgrade, cancellations, prorated billing).
- **Phase 4:** Background scheduler with Celery & Celery Beat for automatic recurring invoice generation and dunning management.
- **Phase 5:** PDF invoice rendering via ReportLab and automated email dispatching.
- **Phase 6:** Frontend analytics dashboard with Recharts (MRR, ARR, active subscriptions, churn analysis).

