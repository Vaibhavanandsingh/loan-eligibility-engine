-- Loan Eligibility Engine: core schema.
-- Idempotent: safe to run repeatedly (CREATE ... IF NOT EXISTS everywhere).

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() on PG < 13

-- One row per uploaded CSV. Created by get_upload_url, finalised by process_csv.
CREATE TABLE IF NOT EXISTS upload_batches (
  batch_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  s3_key         TEXT NOT NULL,
  original_name  TEXT,
  status         TEXT NOT NULL DEFAULT 'PENDING_UPLOAD'
                 CHECK (status IN ('PENDING_UPLOAD', 'PROCESSING', 'COMPLETED',
                                   'WEBHOOK_PENDING', 'MATCHING', 'MATCHED',
                                   'NOTIFIED', 'FAILED')),
  total_rows     INT NOT NULL DEFAULT 0,
  valid_rows     INT NOT NULL DEFAULT 0,
  invalid_rows   INT NOT NULL DEFAULT 0,
  error_message  TEXT,
  -- n8n execution id of the Workflow B run matching this batch, so the error
  -- workflow can mark exactly this batch FAILED if that run crashes.
  matching_execution_id TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at   TIMESTAMPTZ
);

-- Users are upserted by user_id; batch_id always points at the latest batch
-- that touched the row, which is how Workflow B finds "newly added" users.
-- Email is NOT unique: the sample data reuses addresses across user_ids.
CREATE TABLE IF NOT EXISTS users (
  user_id            TEXT PRIMARY KEY,
  name               TEXT,
  email              TEXT NOT NULL,
  monthly_income     NUMERIC(12,2) NOT NULL CHECK (monthly_income >= 0),
  credit_score       INT NOT NULL CHECK (credit_score BETWEEN 300 AND 900),
  employment_status  TEXT NOT NULL
                     CHECK (employment_status IN ('salaried', 'self_employed', 'business', 'unemployed', 'other')),
  age                INT NOT NULL CHECK (age BETWEEN 18 AND 100),
  batch_id           UUID REFERENCES upload_batches(batch_id),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Populated by Workflow A (crawler) or the seed file.
CREATE TABLE IF NOT EXISTS loan_products (
  product_id          SERIAL PRIMARY KEY,
  source_site         TEXT NOT NULL,
  product_name        TEXT NOT NULL,
  provider            TEXT,
  interest_rate_min   NUMERIC(5,2),
  interest_rate_max   NUMERIC(5,2),
  loan_amount_min     NUMERIC(14,2),
  loan_amount_max     NUMERIC(14,2),
  min_monthly_income  NUMERIC(12,2),
  min_credit_score    INT,
  max_credit_score    INT,
  min_age             INT,
  max_age             INT,
  allowed_employment  TEXT[],          -- NULL = any employment type
  extra_criteria_text TEXT,            -- rules the columns above cannot express
  -- extra_criteria_text translated once by the LLM into a machine-checkable rule
  -- (see Workflow B). compiled_rules_hash = md5 of the text it was compiled from,
  -- so an edited or re-crawled rule is recompiled automatically.
  compiled_rules      JSONB,
  compiled_rules_hash TEXT,
  compiled_at         TIMESTAMPTZ,
  source_url          TEXT,
  content_hash        TEXT,            -- hash of the crawled page, for change detection
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (source_site, product_name)
);

-- Pages Workflow A crawls, plus per-page state so it can skip unchanged pages
-- without an LLM call. Add or disable a source here; no workflow edit needed.
CREATE TABLE IF NOT EXISTS crawl_sources (
  source_site         TEXT PRIMARY KEY,
  url                 TEXT NOT NULL,
  enabled             BOOLEAN NOT NULL DEFAULT TRUE,
  content_hash        TEXT,          -- SHA-256 of the cleaned page text last extracted
  last_crawled_at     TIMESTAMPTZ,
  last_status         TEXT,          -- extracted | unchanged | fetch_failed | extract_failed | no_content
  last_error          TEXT,
  last_product_count  INT
);

CREATE TABLE IF NOT EXISTS matches (
  match_id     BIGSERIAL PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  product_id   INT  NOT NULL REFERENCES loan_products(product_id) ON DELETE CASCADE,
  batch_id     UUID REFERENCES upload_batches(batch_id),
  -- HARD_RULES:     product has no text rule; passing the SQL columns is enough
  -- COMPILED_RULES: product's text rule was checked via its compiled form
  -- UNVERIFIED:     text rule could not be compiled (e.g. LLM unavailable); flagged
  match_stage  TEXT NOT NULL CHECK (match_stage IN ('HARD_RULES', 'COMPILED_RULES', 'UNVERIFIED')),
  score        NUMERIC(5,2),
  -- Facts the rule depends on that the CSV does not contain (e.g. employer_category).
  needs_verification TEXT[] NOT NULL DEFAULT '{}',
  match_note   TEXT,                     -- shown in the email, e.g. what will be verified
  notified     BOOLEAN NOT NULL DEFAULT FALSE,
  notified_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, product_id)
);

-- Every email Workflow C builds. In test mode the recipient is always a test
-- alias; the user's own address is kept only masked, for the record.
CREATE TABLE IF NOT EXISTS email_outbox (
  email_id             BIGSERIAL PRIMARY KEY,
  batch_id             UUID REFERENCES upload_batches(batch_id),
  user_id              TEXT REFERENCES users(user_id) ON DELETE CASCADE,
  original_email       TEXT,          -- masked, e.g. d***@example.com
  recipient            TEXT NOT NULL, -- where it was (or would be) sent
  subject              TEXT NOT NULL,
  html                 TEXT NOT NULL,
  product_ids          INT[] NOT NULL,
  mode                 TEXT NOT NULL CHECK (mode IN ('dry_run', 'ses')),
  status               TEXT NOT NULL CHECK (status IN ('DRY_RUN', 'SENT', 'FAILED')),
  provider_message_id  TEXT,          -- SES MessageId when sent
  error                TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (batch_id, user_id, mode)
);

-- Every failed n8n execution of Workflows A or B, written by the error workflow (E).
CREATE TABLE IF NOT EXISTS workflow_errors (
  error_id       BIGSERIAL PRIMARY KEY,
  workflow_id    TEXT,
  workflow_name  TEXT,
  node_name      TEXT,           -- the step that failed
  message        TEXT,
  execution_id   TEXT,
  execution_url  TEXT,           -- opens the failed run in the n8n editor
  batch_id       UUID REFERENCES upload_batches(batch_id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per Workflow B run: the numbers behind the optimization funnel.
CREATE TABLE IF NOT EXISTS match_runs (
  run_id              BIGSERIAL PRIMARY KEY,
  batch_id            UUID REFERENCES upload_batches(batch_id),
  users_in_batch      INT,
  active_products     INT,
  raw_pairs           BIGINT,   -- users x products
  sql_candidates      INT,      -- pairs passing the SQL hard rules
  hard_rule_only      INT,      -- ... whose product has no text rule
  text_rule_pairs     INT,      -- ... whose product has a text rule
  text_rule_rejected  INT,      -- ... removed by a compiled rule
  unverified_pairs    INT,      -- ... whose rule could not be compiled
  after_top_k         INT,      -- matches kept (top K per user)
  rules_compiled      INT,      -- products compiled by the LLM in this run
  llm_calls           INT,
  matches_inserted    INT,
  duration_ms         INT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
