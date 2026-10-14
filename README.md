# Candidate kit

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.
# Employee Attendance & Analytics API

A FastAPI application backed by MongoDB for employee management, attendance tracking, manual attendance corrections, and attendance analytics.

## Requirements

- Python 3.10+
- MongoDB 6.0 or newer (MongoDB Atlas is supported)

## Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Create `.env` using `.env.example` as a template, then configure `MONGO_URI` and `MONGO_DB` with your MongoDB connection details.

## Run

```bash
python -m uvicorn app.main:app --port 8000 --reload
```

Open `http://127.0.0.1:8000/docs` to explore and test the API.

## Tests

```bash
python -m pytest -q
```

## Project files

- `app/main.py` — API endpoints, business rules, MongoDB queries, aggregations, and index creation.
- `test_helpers.py` — helper-function tests.
- `openapi.yaml` — API contract and business rules.
- `DATA_MODEL.md` — MongoDB data model.
- `REVIEW.md` — implementation review.
- `DECISIONS.md` — engineering decisions.

## Submission security

Do not commit `.env`, credentials, or other secrets. The `.env.example` file contains placeholder configuration only.
