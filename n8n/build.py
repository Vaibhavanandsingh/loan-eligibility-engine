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
    "app_db": {"postgres": {"id": "2wix96Y76qyvwMAI", "name": "App Db(local)"}},
    "webhook_secret": {"httpHeaderAuth": {"id": "lnWebhookSecret1", "name": "Webhook secret (Lambda to n8n)"}},
    "gemini": {"httpHeaderAuth": {"id": "lnGeminiApiKey01", "name": "Gemini API key"}},
}


class Workflow:
    """Tiny builder: nodes are laid out left to right in the order they are added."""

    def __init__(self, wf_id: str, name: str):
        self.id, self.name = wf_id, name
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
            "settings": {"executionOrder": "v1", "saveManualExecutions": True},
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

SQL_START_RUN = """
-- Mark the batch as matching, clear its un-notified matches so a re-run starts
-- clean (already-emailed matches are kept), and count what we are matching.
WITH upd AS (
  UPDATE upload_batches SET status = 'MATCHING' WHERE batch_id = $1::uuid RETURNING 1
), cleared AS (
  DELETE FROM matches WHERE batch_id = $1::uuid AND NOT notified RETURNING 1
)
SELECT (SELECT count(*) FROM upd)::int                                  AS batch_found,
       (SELECT count(*) FROM cleared)::int                              AS cleared_matches,
       (SELECT count(*) FROM users WHERE batch_id = $1::uuid)::int      AS users_in_batch,
       (SELECT count(*) FROM loan_products WHERE is_active)::int        AS active_products;
"""

SQL_LOAD_STALE_RULES = """
-- Products whose text rule has never been compiled, or changed since it was.
SELECT product_id, product_name, extra_criteria_text, md5(extra_criteria_text) AS text_hash
  FROM loan_products
 WHERE is_active
   AND extra_criteria_text IS NOT NULL
   AND compiled_rules_hash IS DISTINCT FROM md5(extra_criteria_text)
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

SQL_LOAD_PRODUCTS = """
SELECT product_id, product_name, provider,
       interest_rate_min::float8  AS interest_rate_min,
       min_monthly_income::float8 AS min_monthly_income,
       min_credit_score, extra_criteria_text, compiled_rules,
       compiled_rules_hash IS NOT DISTINCT FROM md5(extra_criteria_text) AS compiled_current
  FROM loan_products
 WHERE is_active;
"""

# Stage 1: the set-based pre-filter. One JOIN in Postgres removes every pair that
# fails a numeric or employment rule, and returns one row per user (not per pair)
# to keep the payload n8n has to hold small.
SQL_LOAD_CANDIDATES = """
SELECT u.user_id, u.monthly_income::float8 AS income, u.credit_score, u.age, u.employment_status,
       array_agg(p.product_id ORDER BY p.product_id) AS product_ids
  FROM users u
  JOIN loan_products p
    ON p.is_active
   AND u.monthly_income >= COALESCE(p.min_monthly_income, 0)
   AND u.credit_score   >= COALESCE(p.min_credit_score, 300)
   AND (p.max_credit_score IS NULL OR u.credit_score <= p.max_credit_score)
   AND u.age BETWEEN COALESCE(p.min_age, 18) AND COALESCE(p.max_age, 100)
   AND (p.allowed_employment IS NULL OR u.employment_status = ANY (p.allowed_employment))
 WHERE u.batch_id = $1::uuid
 GROUP BY u.user_id;
"""

SQL_INSERT_MATCHES = """
=WITH data AS (
  SELECT * FROM jsonb_to_recordset({{ $json.sql_payload }}::jsonb)
    AS d(user_id text, product_id int, match_stage text, score numeric,
         needs_verification text[], match_note text)
), ins AS (
  INSERT INTO matches (user_id, product_id, batch_id, match_stage, score, needs_verification, match_note)
  SELECT user_id, product_id, '{{ $json.batch_id }}'::uuid, match_stage, score,
         COALESCE(needs_verification, '{}'), match_note
    FROM data
  ON CONFLICT (user_id, product_id) DO UPDATE
     SET batch_id = EXCLUDED.batch_id, match_stage = EXCLUDED.match_stage, score = EXCLUDED.score,
         needs_verification = EXCLUDED.needs_verification, match_note = EXCLUDED.match_note
   WHERE NOT matches.notified           -- never touch a match that was already emailed
  RETURNING 1
)
SELECT count(*)::int AS inserted FROM ins;
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
SELECT run_id FROM run;
"""


def build_matching() -> Workflow:
    wf = Workflow("lnMatchingWflwB1", "B - User-Loan Matching")
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
    candidates = wf.postgres("Load candidates (SQL pre-filter)", SQL_LOAD_CANDIDATES, pos=at(12, -1),
                             replacement=BATCH_ID, executeOnce=True, alwaysOutputData=True)
    evaluate = wf.code("Evaluate and rank", "B/evaluate_and_rank.js", pos=at(13, -1))
    insert = wf.postgres("Insert matches", SQL_INSERT_MATCHES, pos=at(14, -1))
    finish = wf.code("Finish run", "B/finish_run.js", pos=at(15, -1))
    record = wf.postgres("Record run", SQL_RECORD_RUN, pos=at(16, -1))
    handoff = wf.node("Hand off to Workflow C", "noOp", 1, {}, pos=at(17, -1),
                      notes="Replaced by an Execute Workflow call once Workflow C exists.")

    wf.chain(webhook, validate, is_valid)
    wf.connect(is_valid, accepted, 0)
    wf.connect(is_valid, rejected, 1)
    wf.chain(accepted, start, stale, build_req, any_rules)
    wf.connect(any_rules, gemini, 0)
    wf.connect(any_rules, products, 1)  # nothing to compile: skip the LLM entirely
    wf.chain(gemini, parse, save, products, candidates, evaluate, insert, finish, record, handoff)
    return wf


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for wf, filename in [(build_matching(), "B_matching.json")]:
        path = OUT / filename
        path.write_text(json.dumps(wf.to_json(), indent=2) + "\n")
        print(f"wrote {path.relative_to(HERE.parent)} ({len(wf.nodes)} nodes)")
