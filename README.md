# SentinelScrape

> **The Trust & Resilience Layer between Bright Data Scraper Studio and Gemini AI.**

---

## 📖 Overview

**SentinelScrape** is an intelligent orchestration and trust verification engine designed to sit between automated web scrapers (such as Bright Data Scraper Studio) and downstream LLM reasoning workflows (such as Google Gemini). 

It ensures high reliability for AI pipelines by validating scraped data against contracts, generating deterministic failure signatures, calculating real-time trust scores, and coordinating tiered self-healing recovery strategies when data issues arise.

---

## 🎯 Role 2 Responsibilities: AI & Backend Integration Engineer

Role 2 owns the core backend foundations, API infrastructure, orchestration flows, and integration bridges:

- **Shared Data Contracts**: Maintain unified models (e.g. `SentinelResponse`) ensuring interoperability between data scrapers (Role 1), backend engines (Role 2), and user interfaces (Role 3).
- **Backend Architecture & APIs**: Implement FastAPI service routes, dependency injection, and health monitoring endpoints.
- **Workflow Orchestration**: Coordinate the validation, diagnosis, healing, and AI evaluation lifecycle.
- **AI & Integrations**: Build integration pipelines for Bright Data Scraper Studio and Google Gemini.
- **Cost & Ledger Tracking**: Track compute tiers, token usage, and execution costs per request.

---

## 🏗️ Current Backend Architecture

```
SentinelScrape/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app initialization and router mounting
│   ├── models/              # Shared data contracts (SentinelResponse)
│   ├── api/                 # API endpoints (GET /health)
│   ├── orchestrator/        # Flow coordinator and state transition logic
│   ├── validation/          # Schema contract validation and data checks
│   ├── diagnosis/           # Failure signature generation and error mapping
│   ├── healing/             # Tiered self-healing remediations
│   ├── integrations/        # Bright Data Scraper Studio connectors
│   ├── ai/                  # Gemini AI reasoning engines and prompts
│   ├── cache/               # Caching layer for responses and fingerprints
│   └── ledger/              # Cost ledger and token consumption auditing
├── tests/                   # Pytest test suite for models and endpoints
├── .env.example             # Environment variable template
├── .gitignore               # Git ignore configuration
└── requirements.txt         # Project dependencies
```

---

## ⚙️ Installation Instructions

### 1. Prerequisites
- Python 3.10+
- `pip` or virtual environment manager (`venv`)

### 2. Setup Virtual Environment
```bash
# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the `.env.example` file to `.env` and provide your API keys:
```bash
cp .env.example .env
```

---

## 🚀 Running the FastAPI Server

Start the development server using `uvicorn`:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API Base URL: `http://localhost:8000`
- Interactive API Docs (Swagger UI): `http://localhost:8000/docs`
- Alternative API Docs (ReDoc): `http://localhost:8000/redoc`
- Health Check: `http://localhost:8000/health`

---

## 🧪 Running Tests

Execute the unit test suite with `pytest`:

```bash
pytest -v
```