// Stage 5: assemble the funnel report for match_runs and the execution log.

const request = $('Validate request').first().json;
const start = $('Start run').first().json;
const stats = $input.first().json; // one row of counts from "Run matching in database"
const inserted = Number(stats.matches_inserted ?? 0);

let rulesCompiled = 0;
let llmCalls = 0;
let llmError = null;
const compileRequest = $('Build compile request').first().json;
if (compileRequest.count > 0) {
  llmCalls = 1;
  const parsed = $('Parse compiled rules').first().json;
  rulesCompiled = parsed.saved ?? 0;
  llmError = parsed.llm_ok ? null : parsed.error;
}

const funnel = {
  batch_id: request.batch_id,
  users_in_batch: Number(start.users_in_batch),
  active_products: Number(start.active_products),
  raw_pairs: Number(start.users_in_batch) * Number(start.active_products),
  sql_candidates: stats.sql_candidates,
  hard_rule_only: stats.hard_rule_only,
  text_rule_pairs: stats.text_rule_pairs,
  text_rule_rejected: stats.text_rule_rejected,
  unverified_pairs: stats.unverified_pairs,
  after_top_k: stats.after_top_k,
  matched_users: stats.matched_users,
  rules_compiled: rulesCompiled,
  llm_calls: llmCalls,
  matches_inserted: inserted,
  duration_ms: Date.now() - request.started_at,
  llm_error: llmError,
};

return [{ json: funnel }];
