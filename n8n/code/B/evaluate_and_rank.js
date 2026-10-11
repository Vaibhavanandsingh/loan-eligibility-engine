// Stages 3-4: apply compiled text rules, score, keep the top K products per user.
//
// Input: one item per user with the product_ids that passed the SQL hard rules.
// No LLM calls happen here: each product's text rule was compiled once, so
// checking it for a user is a few comparisons.

const TOP_K = 5;
const CHUNK_SIZE = 5000; // match rows per INSERT statement

const batchId = $('Validate request').first().json.batch_id;
const products = new Map($('Load products').all().map((i) => [i.json.product_id, i.json]));
const users = $input.all().map((i) => i.json).filter((u) => u.user_id);

// True when the user meets every threshold in a compiled requirement.
function meets(req, u) {
  if (!req) return true;
  if (req.credit_score_min != null && u.credit_score < req.credit_score_min) return false;
  if (req.monthly_income_min != null && u.income < req.monthly_income_min) return false;
  if (req.age_min != null && u.age < req.age_min) return false;
  if (req.age_max != null && u.age > req.age_max) return false;
  if (req.employment_in?.length && !req.employment_in.includes(u.employment_status)) return false;
  return true;
}

function applyRule(rule, u) {
  if (rule.kind === 'informational') return { pass: true, verify: [], note: null };
  if (!meets(rule.require, u)) return { pass: false };
  for (const c of rule.conditions ?? []) {
    if (c.when_employment_in.includes(u.employment_status) && !meets(c.require, u)) {
      return { pass: false };
    }
  }
  const verify = rule.needs_unknown_data ?? [];
  return { pass: true, verify, note: verify.length ? rule.verification_note || null : null };
}

// 0-100: headroom over the credit and income minimums, cheaper rates, and
// certainty (no facts left to verify). Weights favour how safely the user qualifies.
function score(u, p, verify) {
  const credit = p.min_credit_score ? Math.min(1, (u.credit_score - p.min_credit_score) / 100) : 0.5;
  const income = p.min_monthly_income ? Math.min(1, u.income / p.min_monthly_income - 1) : 0.5;
  const rate = p.interest_rate_min != null
    ? Math.max(0, Math.min(1, (36 - p.interest_rate_min) / 26))
    : 0.5;
  const certain = verify.length ? 0 : 1;
  return Math.round((40 * credit + 30 * income + 20 * rate + 10 * certain) * 100) / 100;
}

const stats = {
  sql_candidates: 0,
  hard_rule_only: 0,
  text_rule_pairs: 0,
  text_rule_rejected: 0,
  unverified_pairs: 0,
};
const rows = [];

for (const u of users) {
  const kept = [];
  for (const productId of u.product_ids) {
    const p = products.get(productId);
    if (!p) continue;
    stats.sql_candidates++;

    let stage;
    let verify = [];
    let note = null;
    if (!p.extra_criteria_text) {
      stage = 'HARD_RULES';
      stats.hard_rule_only++;
    } else {
      stats.text_rule_pairs++;
      const rule = p.compiled_current ? p.compiled_rules : null;
      if (rule?.status === 'ok') {
        const result = applyRule(rule, u);
        if (!result.pass) {
          stats.text_rule_rejected++;
          continue;
        }
        stage = 'COMPILED_RULES';
        verify = result.verify;
        note = result.note;
      } else {
        // No usable compiled rule (LLM unavailable or output invalid): keep the
        // match but say plainly that the extra conditions were not checked.
        stage = 'UNVERIFIED';
        stats.unverified_pairs++;
        verify = ['text_rule_not_checked'];
        note = `Additional conditions apply: ${p.extra_criteria_text}`;
      }
    }
    kept.push({ product_id: productId, stage, verify, note, score: score(u, p, verify) });
  }

  kept.sort((a, b) => b.score - a.score);
  for (const m of kept.slice(0, TOP_K)) {
    rows.push({
      user_id: u.user_id,
      product_id: m.product_id,
      match_stage: m.stage,
      score: m.score,
      needs_verification: m.verify,
      match_note: m.note,
    });
  }
}

stats.after_top_k = rows.length;
stats.matched_users = new Set(rows.map((r) => r.user_id)).size;

const sqlLiteral = (value) => `'${JSON.stringify(value).replace(/'/g, "''")}'`;
const chunks = [];
for (let i = 0; i < rows.length; i += CHUNK_SIZE) chunks.push(rows.slice(i, i + CHUNK_SIZE));
if (chunks.length === 0) chunks.push([]); // keep the workflow moving when nothing matched

return chunks.map((chunk, index) => ({
  json: {
    batch_id: batchId,
    chunk: index + 1,
    rows: chunk.length,
    sql_payload: sqlLiteral(chunk),
    ...(index === 0 ? { stats } : {}),
  },
}));
