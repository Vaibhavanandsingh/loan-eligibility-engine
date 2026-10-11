# Loan Eligibility Engine

Ingests user CSVs at scale, discovers personal loan products, matches users to the products they are likely eligible for, and emails them. AWS (S3, Lambda, RDS, SES) handles ingestion; a self-hosted n8n instance orchestrates discovery, matching and notification.

> **Work in progress.** Full setup, architecture diagram and design notes will be added here.

## What exists so far

| Area | Status |
|---|---|
| Schema (`sql/`) | Done |
| CSV ingestion: presigned S3 upload, streaming parse, chunked upserts (`src/`) | Written and tested locally; AWS deployment pending |
| Local stack: Postgres + n8n (`docker-compose.yml`) | Running |
| Workflow B: matching (`n8n/`) | Built, being tested |
| Workflow A (crawler), Workflow C (email), upload UI, `serverless.yml` | Not started |

## Run locally

```bash
cp .env.example .env               # then set N8N_ENCRYPTION_KEY (openssl rand -hex 32)
docker compose up -d               # app Postgres on :5433, n8n on :5678
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                   # unit + database tests
.venv/bin/python scripts/local_ingest.py data/users.csv
```

## Matching approach in one paragraph

A SQL join removes every user-product pair that fails a numeric or employment rule. Free-text product rules ("self-employed need a 750 score") are translated by the LLM **once per product** into structured checks, cached against a hash of the rule text, and then applied in plain code. On the 10,000-user sample with 20 products that means 1 LLM request per change in product rules instead of tens of thousands of per-pair calls.
