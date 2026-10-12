"""Assemble the n8n workflow JSON files from readable sources.

Code-node JavaScript lives in n8n/code/<workflow>/*.js and SQL lives here, next
to the node that runs it. This script stitches them into the importable JSON in
n8n/workflows/. Re-run it after editing any source, then re-import:

    python n8n/build.py
    docker exec loan-engine-n8n n8n import:workflow --input=/workflows/B_matching.json

Credential ids below belong to the local n8n instance. On another instance,
n8n asks you to pick the matching credential when the workflow is opened.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE / "code"
OUT = HERE / "workflows"

CREDENTIALS = {
    "app_db": {"postgres": {"id": "2wix96Y76qyvwMAI", "name": "App DB"}},
    "webhook_secret": {"httpHeaderAuth": {"id": "lnWebhookSecret1", "name": "Webhook secret (Lambda to n8n)"}},
    "gemini": {"httpHeaderAuth": {"id": "lnGeminiApiKey01", "name": "Gemini API key"}},
    "aws_ses": {"aws": {"id": "lnAwsSesSender01", "name": "AWS SES (send only)"}},
}


class Workflow:
    """Tiny builder: nodes are laid out left to right in the order they are added."""

    def __init__(self, wf_id: str, name: str, error_workflow: str | None = None):
        self.id, self.name, self.error_workflow = wf_id, name, error_workflow
        self.nodes: list[dict] = []
        self.connections: dict[str, dict] = {}

    def node(self, name: str, type_: str, version: float, params: dict, *, pos: tuple[int, int],
             credentials: str | None = None, **settings) -> str:
        node = {
            "id": f"{self.id}-{len(self.nodes) + 1:02d}",
            "name": name,
            "type": f"n8n-nodes-base.{type_}",
            "typeVersion": version,
            "position": list(pos),
            "parameters": params,
            **settings,
        }
        if credentials:
            node["credentials"] = CREDENTIALS[credentials]
        self.nodes.append(node)
        return name

    def code(self, name: str, file: str, *, pos: tuple[int, int], **settings) -> str:
        js = (CODE / file).read_text()
        return self.node(name, "code", 2, {"jsCode": js}, pos=pos, **settings)

    def postgres(self, name: str, query: str, *, pos: tuple[int, int], replacement: str | None = None,
                 **settings) -> str:
        params: dict = {"operation": "executeQuery", "query": query.strip(), "options": {}}
        if replacement:
            params["options"]["queryReplacement"] = replacement
        return self.node(name, "postgres", 2.6, params, pos=pos, credentials="app_db", **settings)

    def connect(self, src: str, dst: str, output: int = 0) -> None:
        outputs = self.connections.setdefault(src, {"main": []})["main"]
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": dst, "type": "main", "index": 0})

    def chain(self, *names: str) -> None:
        for a, b in zip(names, names[1:]):
            self.connect(a, b)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {
                "executionOrder": "v1",
                "saveManualExecutions": True,
                **({"errorWorkflow": self.error_workflow} if self.error_workflow else {}),
            },
            "active": False,
        }


def if_node(wf: Workflow, name: str, left: str, operator: dict, right=None, *, pos) -> str:
    condition = {"id": f"{name}-cond", "leftValue": left, "rightValue": right if right is not None else "",
                 "operator": operator}
    params = {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [condition],
            "combinator": "and",
        },
        "options": {},
    }
    return wf.node(name, "if", 2.2, params, pos=pos)


# --------------------------------------------------------------------------- #
# Workflow B: user-loan matching
# --------------------------------------------------------------------------- #

BATCH_ID = "={{ $('Validate request').first().json.batch_id }}"

# Fingerprint of a product's rule text as compiled. Bump RULES_COMPILER_VERSION
# whenever the compile prompt or schema changes: every rule then no longer
# matches its stored hash and is recompiled on the next run.
RULES_COMPILER_VERSION = "v2"
RULE_HASH = f"md5('{RULES_COMPILER_VERSION}:' || extra_criteria_text)"

SQL_START_RUN = """
=-- Mark the batch as matching and remember this execution's id (the error
-- workflow uses it to fail exactly this batch if the run crashes). Clear its
-- un-notified matches so a re-run starts clean; already-emailed matches stay.
WITH upd AS (
  UPDATE upload_batches
     SET status = 'MATCHING', error_message = NULL, matching_execution_id = '{{ $execution.id }}'
   WHERE batch_id = $1::uuid
  RETURNING 1
), cleared AS (
  DELETE FROM matches WHERE batch_id = $1::uuid AND NOT notified RETURNING 1
)
SELECT (SELECT count(*) FROM upd)::int                                  AS batch_found,
       (SELECT count(*) FROM cleared)::int                              AS cleared_matches,
       (SELECT count(*) FROM users WHERE batch_id = $1::uuid)::int      AS users_in_batch,
       (SELECT count(*) FROM loan_products WHERE is_active)::int        AS active_products;
"""

SQL_LOAD_STALE_RULES = f"""
-- Products whose text rule has never been compiled, or changed since it was
-- (or the compiler itself changed: see RULES_COMPILER_VERSION).
SELECT product_id, product_name, extra_criteria_text, {RULE_HASH} AS text_hash
  FROM loan_products
 WHERE is_active
   AND extra_criteria_text IS NOT NULL
   AND compiled_rules_hash IS DISTINCT FROM {RULE_HASH}
 ORDER BY product_id;
"""

SQL_SAVE_COMPILED = """
=WITH data AS (
  SELECT * FROM jsonb_to_recordset({{ $json.sql_payload }}::jsonb)
    AS d(product_id int, text_hash text, compiled jsonb)
), upd AS (
  UPDATE loan_products p
     SET compiled_rules = d.compiled, compiled_rules_hash = d.text_hash, compiled_at = now()
    FROM data d
   WHERE p.product_id = d.product_id
  RETURNING 1
)
SELECT count(*)::int AS rules_saved FROM upd;
"""

SQL_LOAD_PRODUCTS = f"""
SELECT product_id, product_name, provider,
       interest_rate_min::float8  AS interest_rate_min,
       min_monthly_income::float8 AS min_monthly_income,
       min_credit_score, extra_criteria_text, compiled_rules,
       compiled_rules_hash IS NOT DISTINCT FROM {RULE_HASH} AS compiled_current
  FROM loan_products
 WHERE is_active;
"""

SQL_RECORD_RUN = """
=WITH run AS (
  INSERT INTO match_runs (batch_id, users_in_batch, active_products, raw_pairs, sql_candidates,
                          hard_rule_only, text_rule_pairs, text_rule_rejected, unverified_pairs,
                          after_top_k, rules_compiled, llm_calls, matches_inserted, duration_ms)
  VALUES ('{{ $json.batch_id }}'::uuid, {{ $json.users_in_batch }}, {{ $json.active_products }},
          {{ $json.raw_pairs }}, {{ $json.sql_candidates }}, {{ $json.hard_rule_only }},
          {{ $json.text_rule_pairs }}, {{ $json.text_rule_rejected }}, {{ $json.unverified_pairs }},
          {{ $json.after_top_k }}, {{ $json.rules_compiled }}, {{ $json.llm_calls }},
          {{ $json.matches_inserted }}, {{ $json.duration_ms }})
  RETURNING run_id
), upd AS (
  UPDATE upload_batches SET status = 'MATCHED' WHERE batch_id = '{{ $json.batch_id }}'::uuid RETURNING 1
)
SELECT run_id, '{{ $json.batch_id }}'::uuid AS batch_id FROM run;
"""


def build_matching() -> Workflow:
    wf = Workflow("lnMatchingWflwB1", "B - User-Loan Matching", error_workflow=ERROR_WORKFLOW_ID)
    y, x = 300, 0
    step = 240

    def at(col: int, row: int = 0) -> tuple[int, int]:
        return (x + col * step, y + row * 180)

    webhook = wf.node(
        "Webhook: batch ingested", "webhook", 2,
        {"httpMethod": "POST", "path": "loan-match", "authentication": "headerAuth",
         "responseMode": "responseNode", "options": {}},
        pos=at(0), credentials="webhook_secret", webhookId="5c1b7a2e-9d4f-4b8e-a3c6-1f2e8d9b0a71",
    )
    validate = wf.code("Validate request", "B/validate_request.js", pos=at(1))
    is_valid = if_node(wf, "Valid batch_id?", "={{ $json.valid }}",
                       {"type": "boolean", "operation": "true", "singleValue": True}, pos=at(2))
    accepted = wf.node(
        "Respond 202 Accepted", "respondToWebhook", 1.1,
        {"respondWith": "json",
         "responseBody": "={{ JSON.stringify({ accepted: true, batch_id: $json.batch_id }) }}",
         "options": {"responseCode": 202}},
        pos=at(3, -1),
    )
    rejected = wf.node(
        "Respond 400 Bad Request", "respondToWebhook", 1.1,
        {"respondWith": "json",
         "responseBody": "={{ JSON.stringify({ accepted: false, error: $json.error }) }}",
         "options": {"responseCode": 400}},
        pos=at(3, 1),
    )
    start = wf.postgres("Start run", SQL_START_RUN, pos=at(4, -1), replacement=BATCH_ID)
    stale = wf.postgres("Load stale rules", SQL_LOAD_STALE_RULES, pos=at(5, -1), alwaysOutputData=True)
    build_req = wf.code("Build compile request", "B/build_compile_request.js", pos=at(6, -1))
    any_rules = if_node(wf, "Any rules to compile?", "={{ $json.count }}",
                        {"type": "number", "operation": "gt"}, 0, pos=at(7, -1))
    gemini = wf.node(
        "Gemini: compile rules", "httpRequest", 4.2,
        {"method": "POST",
         "url": "=https://generativelanguage.googleapis.com/v1beta/models/{{ $json.model }}:generateContent",
         "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
         "sendBody": True, "specifyBody": "json",
         "jsonBody": "={{ JSON.stringify($json.request_body) }}",
         "options": {"timeout": 120000}},
        pos=at(8, -2), credentials="gemini",
        retryOnFail=True, maxTries=3, waitBetweenTries=5000, onError="continueRegularOutput",
    )
    parse = wf.code("Parse compiled rules", "B/parse_compiled_rules.js", pos=at(9, -2))
    save = wf.postgres("Save compiled rules", SQL_SAVE_COMPILED, pos=at(10, -2))
    products = wf.postgres("Load products", SQL_LOAD_PRODUCTS, pos=at(11, -1))
    # Stages 1-4 run inside Postgres: n8n generates the statement from the compiled
    # rules, so no user or match rows travel between n8n and the database.
    plan = wf.code("Plan matching SQL", "B/plan_matching_sql.js", pos=at(12, -1))
    run = wf.postgres("Run matching in database", "={{ $json.sql }}", pos=at(13, -1))
    finish = wf.code("Finish run", "B/finish_run.js", pos=at(14, -1))
    record = wf.postgres("Record run", SQL_RECORD_RUN, pos=at(15, -1))
    handoff = wf.node(
        "Start Workflow C (notify)", "executeWorkflow", 1.2,
        {"source": "database",
         "workflowId": {"__rl": True, "value": NOTIFY_WORKFLOW_ID, "mode": "id"},
         "workflowInputs": {"mappingMode": "defineBelow", "value": {}, "matchingColumns": [], "schema": [],
                            "attemptToConvertTypes": False, "convertFieldsToString": True},
         "mode": "once",
         # Matching is finished and recorded; notification runs on its own.
         "options": {"waitForSubWorkflow": False}},
        pos=at(16, -1),
    )

    wf.chain(webhook, validate, is_valid)
    wf.connect(is_valid, accepted, 0)
    wf.connect(is_valid, rejected, 1)
    wf.chain(accepted, start, stale, build_req, any_rules)
    wf.connect(any_rules, gemini, 0)
    wf.connect(any_rules, products, 1)  # nothing to compile: skip the LLM entirely
    wf.chain(gemini, parse, save, products, plan, run, finish, record, handoff)
    return wf


# --------------------------------------------------------------------------- #
# Workflow A: loan product discovery (crawler)
# --------------------------------------------------------------------------- #

CRAWLER_USER_AGENT = "LoanEligibilityEngineBot/0.1 (student assignment; polite, once per day)"

SQL_LOAD_SOURCES = """
SELECT source_site, url, content_hash AS previous_hash
  FROM crawl_sources
 WHERE enabled
 ORDER BY source_site;
"""

SQL_RECORD_SKIP = """
=UPDATE crawl_sources
    SET last_crawled_at = now(), last_status = {{ $json.q_status }}, last_error = {{ $json.q_error }}
  WHERE source_site = {{ $json.q_site }}
RETURNING source_site, last_status;
"""

# Upsert this page's products, retire products the page no longer lists (only
# when extraction succeeded), and store the page hash only on success.
SQL_SAVE_PRODUCTS = """
=WITH data AS (
  SELECT * FROM jsonb_to_recordset({{ $json.products_sql }}::jsonb) AS d(
    product_name text, provider text, interest_rate_min numeric, interest_rate_max numeric,
    loan_amount_min numeric, loan_amount_max numeric, min_monthly_income numeric,
    min_credit_score int, max_credit_score int, min_age int, max_age int,
    allowed_employment text[], extra_criteria_text text)
), up AS (
  INSERT INTO loan_products (source_site, product_name, provider, interest_rate_min, interest_rate_max,
                             loan_amount_min, loan_amount_max, min_monthly_income, min_credit_score,
                             max_credit_score, min_age, max_age, allowed_employment,
                             extra_criteria_text, source_url, content_hash, last_seen_at, is_active)
  SELECT {{ $json.q_site }}, product_name, provider, interest_rate_min, interest_rate_max,
         loan_amount_min, loan_amount_max, min_monthly_income, min_credit_score, max_credit_score,
         min_age, max_age, allowed_employment, extra_criteria_text,
         {{ $json.q_url }}, {{ $json.q_hash }}, now(), TRUE
    FROM data
  ON CONFLICT (source_site, product_name) DO UPDATE SET
    provider = EXCLUDED.provider, interest_rate_min = EXCLUDED.interest_rate_min,
    interest_rate_max = EXCLUDED.interest_rate_max, loan_amount_min = EXCLUDED.loan_amount_min,
    loan_amount_max = EXCLUDED.loan_amount_max, min_monthly_income = EXCLUDED.min_monthly_income,
    min_credit_score = EXCLUDED.min_credit_score, max_credit_score = EXCLUDED.max_credit_score,
    min_age = EXCLUDED.min_age, max_age = EXCLUDED.max_age,
    allowed_employment = EXCLUDED.allowed_employment, extra_criteria_text = EXCLUDED.extra_criteria_text,
    source_url = EXCLUDED.source_url, content_hash = EXCLUDED.content_hash,
    last_seen_at = now(), is_active = TRUE
  RETURNING product_id
), retired AS (
  UPDATE loan_products SET is_active = FALSE
   WHERE source_site = {{ $json.q_site }} AND is_active AND {{ $json.q_ok }}
     AND product_id NOT IN (SELECT product_id FROM up)
  RETURNING 1
), src AS (
  UPDATE crawl_sources
     SET last_crawled_at = now(), last_status = {{ $json.q_status }}, last_error = {{ $json.q_error }},
         last_product_count = (SELECT count(*) FROM up),
         content_hash = CASE WHEN {{ $json.q_ok }} THEN {{ $json.q_hash }} ELSE content_hash END
   WHERE source_site = {{ $json.q_site }}
  RETURNING 1
)
SELECT {{ $json.q_site }} AS source_site, {{ $json.q_status }} AS status,
       (SELECT count(*) FROM up)::int AS upserted, (SELECT count(*) FROM retired)::int AS retired,
       (SELECT count(*) FROM src)::int AS source_updated;
"""

# Seed products are a fallback: active only while fewer than 5 crawled products
# are. Idempotent, so it is harmless that both branches lead here.
SQL_SEED_FALLBACK = """
WITH crawled AS (
  SELECT count(*) AS n FROM loan_products WHERE source_site <> 'seed' AND is_active
), upd AS (
  UPDATE loan_products SET is_active = ((SELECT n FROM crawled) < 5)
   WHERE source_site = 'seed'
  RETURNING is_active
)
SELECT (SELECT n FROM crawled)::int AS crawled_active_products,
       COALESCE(bool_or(is_active), FALSE) AS seed_products_active
  FROM upd;
"""


def build_discovery() -> Workflow:
    wf = Workflow("lnDiscoveryWflA1", "A - Loan Product Discovery", error_workflow=ERROR_WORKFLOW_ID)
    step = 240

    def at(col: int, row: int = 0) -> tuple[int, int]:
        return (col * step, 300 + row * 180)

    schedule = wf.node(
        "Daily at 02:00", "scheduleTrigger", 1.2,
        {"rule": {"interval": [{"field": "cronExpression", "expression": "0 2 * * *"}]}},
        pos=at(0, -1),
    )
    crawl_now = wf.node(
        "Webhook: crawl now", "webhook", 2,
        {"httpMethod": "POST", "path": "crawl-now", "authentication": "headerAuth",
         "responseMode": "onReceived", "options": {"responseCode": 202}},
        pos=at(0, 1), credentials="webhook_secret", webhookId="9e4d2c1a-7b3f-4a8e-b6d5-3c2a1f0e9d84",
    )
    sources = wf.postgres("Load sources", SQL_LOAD_SOURCES, pos=at(1), executeOnce=True)
    fetch = wf.node(
        "Fetch page", "httpRequest", 4.2,
        {"method": "GET", "url": "={{ $json.url }}",
         "sendHeaders": True,
         "headerParameters": {"parameters": [
             {"name": "User-Agent", "value": CRAWLER_USER_AGENT},
             {"name": "Accept", "value": "text/html"}]},
         "options": {
             "timeout": 30000,
             "response": {"response": {"responseFormat": "text", "outputPropertyName": "html"}},
             # Politeness: one request at a time, 2 s apart.
             "batching": {"batch": {"batchSize": 1, "batchInterval": 2000}},
         }},
        pos=at(2), retryOnFail=True, maxTries=2, waitBetweenTries=5000, onError="continueRegularOutput",
    )
    clean = wf.code("Clean to loan text", "A/clean_page.js", pos=at(3))
    hash_ = wf.node(
        "SHA-256 of text", "crypto", 1,
        {"action": "hash", "type": "SHA256", "value": "={{ $json.text }}", "dataPropertyName": "text_hash"},
        pos=at(4),
    )
    changed = if_node(
        wf, "Changed since last extraction?",
        "={{ $json.fetch_ok && $json.useful && $json.text_hash !== $json.previous_hash }}",
        {"type": "boolean", "operation": "true", "singleValue": True}, pos=at(5),
    )
    build_req = wf.code("Build extract request", "A/build_extract_request.js", pos=at(6, -1))
    gemini = wf.node(
        "Gemini: extract products", "httpRequest", 4.2,
        {"method": "POST",
         "url": "=https://generativelanguage.googleapis.com/v1beta/models/{{ $json.model }}:generateContent",
         "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
         "sendBody": True, "specifyBody": "json",
         "jsonBody": "={{ JSON.stringify($json.request_body) }}",
         "options": {"timeout": 180000, "batching": {"batch": {"batchSize": 1, "batchInterval": 4000}}}},
        pos=at(7, -1), credentials="gemini",
        retryOnFail=True, maxTries=3, waitBetweenTries=10000, onError="continueRegularOutput",
    )
    validate = wf.code("Validate products", "A/validate_products.js", pos=at(8, -1))
    save = wf.postgres("Save products", SQL_SAVE_PRODUCTS, pos=at(9, -1))
    skip = wf.code("Mark skipped pages", "A/record_skip.js", pos=at(6, 1))
    record_skip = wf.postgres("Record skip", SQL_RECORD_SKIP, pos=at(7, 1))
    fallback = wf.postgres("Seed fallback", SQL_SEED_FALLBACK, pos=at(10), executeOnce=True)

    wf.connect(schedule, sources)
    wf.connect(crawl_now, sources)
    wf.chain(sources, fetch, clean, hash_, changed)
    wf.connect(changed, build_req, 0)
    wf.connect(changed, skip, 1)
    wf.chain(build_req, gemini, validate, save, fallback)
    wf.chain(skip, record_skip, fallback)
    return wf


# --------------------------------------------------------------------------- #
# Workflow C: user notification
# --------------------------------------------------------------------------- #

NOTIFY_WORKFLOW_ID = "lnNotifyWflowC1"

# Test-mode settings, editable in the n8n editor (Settings node).
TEST_INBOX = "vaibhavanandsingh24@gmail.com"
TEST_RECIPIENTS = [f"vaibhavanandsingh24+{n}@gmail.com" for n in ("one", "two", "three")]

# Pick the delivery set inside Postgres: up to N users with un-notified matches,
# one per employment type first (so the test emails differ visibly), best score
# first. Only those users' matches and product details come back to n8n.
SQL_LOAD_DELIVERY_SET = """
WITH pending AS (
  SELECT user_id, max(score) AS best
    FROM matches
   WHERE batch_id = $1::uuid AND NOT notified
   GROUP BY user_id
), ranked AS (
  SELECT p.user_id, p.best,
         row_number() OVER (PARTITION BY u.employment_status ORDER BY p.best DESC, p.user_id) AS r
    FROM pending p JOIN users u USING (user_id)
), chosen AS (
  SELECT user_id FROM ranked ORDER BY r, best DESC, user_id LIMIT $2
)
SELECT u.user_id, u.name, u.email, u.monthly_income::float8 AS income, u.credit_score, u.age,
       u.employment_status,
       json_agg(json_build_object(
         'product_id', lp.product_id, 'product_name', lp.product_name, 'provider', lp.provider,
         'rate_min', lp.interest_rate_min, 'rate_max', lp.interest_rate_max,
         'amount_max', lp.loan_amount_max, 'min_income', lp.min_monthly_income,
         'min_score', lp.min_credit_score, 'score', m.score,
         'verify', m.needs_verification, 'note', m.match_note, 'url', lp.source_url
       ) ORDER BY m.score DESC, lp.product_id) AS products
  FROM chosen c
  JOIN users u USING (user_id)
  JOIN matches m ON m.user_id = u.user_id AND m.batch_id = $1::uuid AND NOT m.notified
  JOIN loan_products lp ON lp.product_id = m.product_id
 GROUP BY u.user_id;
"""

# One outbox row per email (re-runs update it). Matches are marked notified only
# for SENT, i.e. after SES accepted the message, so a failure is retried later.
SQL_RECORD_EMAIL = """
=WITH saved AS (
  INSERT INTO email_outbox (batch_id, user_id, original_email, recipient, subject, html, product_ids,
                            mode, status, provider_message_id, error)
  VALUES ({{ $json.q_batch }}::uuid, {{ $json.q_user }}, {{ $json.q_original }}, {{ $json.q_recipient }},
          {{ $json.q_subject }}, {{ $json.q_html }}, {{ $json.q_products }}, {{ $json.q_mode }},
          {{ $json.q_status }}, {{ $json.q_message_id }}, {{ $json.q_error }})
  ON CONFLICT (batch_id, user_id, mode) DO UPDATE SET
    recipient = EXCLUDED.recipient, subject = EXCLUDED.subject, html = EXCLUDED.html,
    product_ids = EXCLUDED.product_ids, status = EXCLUDED.status,
    provider_message_id = EXCLUDED.provider_message_id, error = EXCLUDED.error, created_at = now()
  RETURNING email_id, status
), notified AS (
  UPDATE matches SET notified = TRUE, notified_at = now()
   WHERE {{ $json.q_status }} = 'SENT'
     AND batch_id = {{ $json.q_batch }}::uuid AND user_id = {{ $json.q_user }}
     AND product_id = ANY ({{ $json.q_products }}) AND NOT notified
  RETURNING 1
)
SELECT (SELECT email_id FROM saved) AS email_id, (SELECT status FROM saved) AS status,
       (SELECT count(*) FROM notified)::int AS matches_notified;
"""

SQL_FINISH_NOTIFICATION = """
=UPDATE upload_batches SET status = 'NOTIFIED'
  WHERE batch_id = '{{ $('Check settings').first().json.batch_id }}'::uuid
    AND status IN ('MATCHED', 'NOTIFIED')
    AND EXISTS (SELECT 1 FROM email_outbox o
                 WHERE o.batch_id = upload_batches.batch_id AND o.status IN ('SENT', 'DRY_RUN'))
RETURNING batch_id, status;
"""


def build_notification() -> Workflow:
    wf = Workflow(NOTIFY_WORKFLOW_ID, "C - User Notification", error_workflow=ERROR_WORKFLOW_ID)
    step = 240

    def at(col: int, row: int = 0) -> tuple[int, int]:
        return (col * step, 300 + row * 180)

    called = wf.node("Called by Workflow B", "executeWorkflowTrigger", 1.1,
                     {"inputSource": "passthrough"}, pos=at(0, -1))
    notify_now = wf.node(
        "Webhook: notify now", "webhook", 2,
        {"httpMethod": "POST", "path": "notify-now", "authentication": "headerAuth",
         "responseMode": "onReceived", "options": {"responseCode": 202}},
        pos=at(0, 1), credentials="webhook_secret", webhookId="3b8e6f2d-1c4a-4f7e-9a2b-6d5c4e3f2a19",
    )

    def assign(name: str, value, type_: str) -> dict:
        return {"id": f"setting-{name}", "name": name, "value": value, "type": type_}

    recipients_expr = "={{ " + json.dumps(TEST_RECIPIENTS) + " }}"
    settings = wf.node(
        "Settings", "set", 3.4,
        {"mode": "manual", "includeOtherFields": False,
         "assignments": {"assignments": [
             assign("batch_id", "={{ $json.batch_id ?? $json.body?.batch_id }}", "string"),
             # dry_run: build and store emails only. ses: send through AWS SES.
             assign("mode", "ses", "string"),
             assign("test_inbox", TEST_INBOX, "string"),
             assign("test_recipients", recipients_expr, "array"),
             # Users per batch who get a delivered email in test mode.
             assign("send_limit", 3, "number"),
             assign("sender_email", TEST_INBOX, "string"),
             assign("sender_name", "Loan Eligibility Engine", "string"),
         ]},
         "options": {}},
        pos=at(1),
    )
    check = wf.code("Check settings", "C/check_settings.js", pos=at(2))
    load = wf.postgres("Load delivery set", SQL_LOAD_DELIVERY_SET, pos=at(3),
                       replacement="={{ $json.batch_id }},{{ $json.send_limit }}")
    build = wf.code("Build emails", "C/build_emails.js", pos=at(4))
    send_for_real = if_node(wf, "Send for real?", "={{ $('Check settings').first().json.mode }}",
                            {"type": "string", "operation": "equals"}, "ses", pos=at(5))
    ses = wf.node(
        "AWS SES: send", "awsSes", 1,
        {"resource": "email", "operation": "send", "isBodyHtml": True,
         "subject": "={{ $json.subject }}", "body": "={{ $json.html }}",
         "fromEmail": "={{ $json.from }}", "toAddresses": ["={{ $json.recipient }}"],
         "additionalFields": {}},
        pos=at(6, -1), credentials="aws_ses",
        # The SES sandbox allows 1 email per second; a throttled send is retried.
        retryOnFail=True, maxTries=3, waitBetweenTries=1500, onError="continueRegularOutput",
    )
    prepare = wf.code("Prepare outbox rows", "C/prepare_outbox_rows.js", pos=at(7))
    record = wf.postgres("Record email", SQL_RECORD_EMAIL, pos=at(8))
    finish = wf.postgres("Finish notification", SQL_FINISH_NOTIFICATION, pos=at(9), executeOnce=True)

    wf.connect(called, settings)
    wf.connect(notify_now, settings)
    wf.chain(settings, check, load, build, send_for_real)
    wf.connect(send_for_real, ses, 0)
    wf.connect(send_for_real, prepare, 1)  # dry run: no SES call
    wf.chain(ses, prepare, record, finish)
    return wf


# --------------------------------------------------------------------------- #
# Workflow E: error handler (set as the error workflow of A and B)
# --------------------------------------------------------------------------- #

ERROR_WORKFLOW_ID = "lnErrorHandlerE1"

# Log the failure; if it was a matching run, mark that run's batch FAILED so the
# upload page stops waiting. Only a batch still MATCHING is touched.
SQL_RECORD_FAILURE = """
=WITH failed_batch AS (
  UPDATE upload_batches
     SET status = 'FAILED', error_message = {{ $json.q_reason }}
   WHERE matching_execution_id = {{ $json.q_execution_id }} AND status = 'MATCHING'
  RETURNING batch_id
), logged AS (
  INSERT INTO workflow_errors (workflow_id, workflow_name, node_name, message,
                               execution_id, execution_url, batch_id)
  VALUES ({{ $json.q_workflow_id }}, {{ $json.q_workflow_name }}, {{ $json.q_node }},
          {{ $json.q_message }}, {{ $json.q_execution_id }}, {{ $json.q_execution_url }},
          (SELECT batch_id FROM failed_batch LIMIT 1))
  RETURNING error_id, batch_id
)
SELECT error_id, batch_id FROM logged;
"""


def build_error_handler() -> Workflow:
    wf = Workflow(ERROR_WORKFLOW_ID, "E - Error Handler")
    trigger = wf.node("On workflow error", "errorTrigger", 1, {}, pos=(0, 300))
    describe = wf.code("Describe failure", "E/describe_failure.js", pos=(240, 300))
    record = wf.postgres("Record failure", SQL_RECORD_FAILURE, pos=(480, 300))
    wf.chain(trigger, describe, record)
    return wf


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for wf, filename in [
        (build_discovery(), "A_loan_discovery.json"),
        (build_matching(), "B_matching.json"),
        (build_notification(), "C_notification.json"),
        (build_error_handler(), "E_error_handler.json"),
    ]:
        path = OUT / filename
        path.write_text(json.dumps(wf.to_json(), indent=2) + "\n")
        print(f"wrote {path.relative_to(HERE.parent)} ({len(wf.nodes)} nodes)")
