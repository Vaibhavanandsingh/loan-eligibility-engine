// Stages 1-4 as ONE statement that runs inside Postgres.
//
// n8n stays the place where matching is decided: this node turns each product's
// compiled rule (stage 2 output), the scoring formula and the top-K cut into
// SQL. Postgres then runs it where the data lives, so 10,000 users and ~43,000
// match rows never travel to n8n and back; only this statement (a few KB) and
// one row of funnel counts do. Measured before this change: 70 of 74 seconds
// were spent shipping rows between n8n and a remote RDS.
//
// Safety: the SQL is assembled only from validated numbers, a fixed set of
// keywords (employment types, facts to verify) and escaped string literals.

const TOP_K = 5;

const batchId = $('Validate request').first().json.batch_id;
if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(batchId)) {
  throw new Error(`refusing to build SQL for batch id ${batchId}`);
}
const products = $input.all().map((i) => i.json).filter((p) => Number.isInteger(p.product_id));

const EMPLOYMENT = new Set(['salaried', 'self_employed', 'business']);
const UNKNOWN_DATA = new Set([
  'employer_category', 'city', 'business_vintage', 'business_turnover',
  'professional_qualification', 'tax_returns', 'other', 'text_rule_not_checked',
]);

const text = (v) => `'${String(v).replace(/'/g, "''")}'`;
const num = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error(`not a number: ${v}`);
  return String(n);
};
const list = (values, allowed) => {
  const ok = (values ?? []).filter((v) => allowed.has(v));
  return ok.length ? `ARRAY[${ok.map(text).join(', ')}]::text[]` : `'{}'::text[]`;
};
const inEmployment = (values) => `u.employment_status IN (${values.filter((v) => EMPLOYMENT.has(v)).map(text).join(', ') || 'NULL'})`;

// One compiled requirement -> SQL boolean (same checks as the old JS `meets`).
function meets(req = {}) {
  const parts = [];
  if (req.credit_score_min != null) parts.push(`u.credit_score >= ${num(req.credit_score_min)}`);
  if (req.monthly_income_min != null) parts.push(`u.monthly_income >= ${num(req.monthly_income_min)}`);
  if (req.age_min != null) parts.push(`u.age >= ${num(req.age_min)}`);
  if (req.age_max != null) parts.push(`u.age <= ${num(req.age_max)}`);
  if (req.employment_in?.length) parts.push(inEmployment(req.employment_in));
  return parts.length ? parts.join(' AND ') : 'TRUE';
}

// Per product: does the pair pass, which stage decided it, what must be verified, and the note.
function judge(p) {
  if (!p.extra_criteria_text) {
    return { pass: 'TRUE', stage: text('HARD_RULES'), verify: `'{}'::text[]`, note: 'NULL::text' };
  }
  const rule = p.compiled_current ? p.compiled_rules : null;
  if (rule?.status !== 'ok') {
    // No usable compiled rule: keep the match, say plainly it was not checked.
    return {
      pass: 'TRUE',
      stage: text('UNVERIFIED'),
      verify: list(['text_rule_not_checked'], UNKNOWN_DATA),
      note: text(`Additional conditions apply: ${p.extra_criteria_text}`),
    };
  }
  if (rule.kind === 'informational') {
    return { pass: 'TRUE', stage: text('COMPILED_RULES'), verify: `'{}'::text[]`, note: 'NULL::text' };
  }
  const conditions = rule.conditions ?? [];
  const pass = [`(${meets(rule.require)})`]
    .concat(conditions.map((c) => `(NOT ${inEmployment(c.when_employment_in)} OR (${meets(c.require)}))`))
    .join(' AND ');

  const top = (rule.needs_unknown_data ?? []).filter((v) => UNKNOWN_DATA.has(v));
  const verifyParts = [list(top, UNKNOWN_DATA)];
  const noteParts = [];
  if (top.length && rule.verification_note) noteParts.push(text(rule.verification_note));
  for (const c of conditions) {
    const extra = (c.needs_unknown_data ?? []).filter((v) => UNKNOWN_DATA.has(v));
    if (!extra.length) continue;
    verifyParts.push(`CASE WHEN ${inEmployment(c.when_employment_in)} THEN ${list(extra, UNKNOWN_DATA)} ELSE '{}'::text[] END`);
    if (c.verification_note) {
      noteParts.push(`CASE WHEN ${inEmployment(c.when_employment_in)} THEN ${text(c.verification_note)} END`);
    }
  }
  return {
    pass,
    stage: text('COMPILED_RULES'),
    // Duplicates removed in SQL, like the JS Set it replaces.
    verify: `ARRAY(SELECT DISTINCT unnest(${verifyParts.join(' || ')}))`,
    note: noteParts.length ? `NULLIF(concat_ws(' ', ${noteParts.join(', ')}), '')` : 'NULL::text',
  };
}

const judged = products.map((p) => ({ id: p.product_id, ...judge(p) }));
const caseOf = (field, fallback) =>
  `CASE u.product_id\n${judged.map((j) => `      WHEN ${j.id} THEN ${j[field]}`).join('\n')}\n      ELSE ${fallback} END`;

const sql = `
WITH cand AS (
  -- Stage 1: hard rules, set-based, scoped to this batch
  SELECT u.user_id, u.employment_status, u.monthly_income, u.credit_score, u.age,
         p.product_id, p.min_credit_score, p.min_monthly_income, p.interest_rate_min
    FROM users u
    JOIN loan_products p
      ON p.is_active
     AND u.monthly_income >= COALESCE(p.min_monthly_income, 0)
     AND u.credit_score   >= COALESCE(p.min_credit_score, 300)
     AND (p.max_credit_score IS NULL OR u.credit_score <= p.max_credit_score)
     AND u.age BETWEEN COALESCE(p.min_age, 18) AND COALESCE(p.max_age, 100)
     AND (p.allowed_employment IS NULL OR u.employment_status = ANY (p.allowed_employment))
   WHERE u.batch_id = '${batchId}'::uuid
), judged AS (
  -- Stage 2: each product's compiled text rule, generated by n8n
  SELECT u.*,
    ${caseOf('pass', 'TRUE')} AS pass,
    ${caseOf('stage', text('HARD_RULES'))} AS stage,
    ${caseOf('verify', `'{}'::text[]`)} AS verify,
    ${caseOf('note', 'NULL::text')} AS note
  FROM cand u
), scored AS (
  -- Stage 3: score 0-100 (credit headroom 40, income headroom 30, rate 20, nothing to verify 10).
  -- CASE, not COALESCE(LEAST(...)): LEAST/GREATEST ignore NULLs in Postgres.
  SELECT j.*, round((
      40 * (CASE WHEN COALESCE(j.min_credit_score, 0) = 0 THEN 0.5
                 ELSE LEAST(1, (j.credit_score - j.min_credit_score) / 100.0) END)
    + 30 * (CASE WHEN COALESCE(j.min_monthly_income, 0) = 0 THEN 0.5
                 ELSE LEAST(1, j.monthly_income / j.min_monthly_income - 1) END)
    + 20 * (CASE WHEN j.interest_rate_min IS NULL THEN 0.5
                 ELSE GREATEST(0, LEAST(1, (36 - j.interest_rate_min) / 26.0)) END)
    + 10 * (CASE WHEN cardinality(j.verify) = 0 THEN 1 ELSE 0 END)
  )::numeric, 2) AS score
  FROM judged j
  WHERE j.pass
), ranked AS (
  -- Stage 4: keep the top ${TOP_K} per user
  SELECT s.*, row_number() OVER (PARTITION BY s.user_id ORDER BY s.score DESC, s.product_id) AS rank
  FROM scored s
), ins AS (
  INSERT INTO matches (user_id, product_id, batch_id, match_stage, score, needs_verification, match_note)
  SELECT user_id, product_id, '${batchId}'::uuid, stage, score, verify, note
    FROM ranked WHERE rank <= ${TOP_K}
  ON CONFLICT (user_id, product_id) DO UPDATE
     SET batch_id = EXCLUDED.batch_id, match_stage = EXCLUDED.match_stage, score = EXCLUDED.score,
         needs_verification = EXCLUDED.needs_verification, match_note = EXCLUDED.match_note
   WHERE NOT matches.notified           -- never touch a match that was already emailed
  RETURNING 1
)
SELECT
  (SELECT count(*) FROM cand)::int                                        AS sql_candidates,
  (SELECT count(*) FROM judged WHERE stage = 'HARD_RULES')::int           AS hard_rule_only,
  (SELECT count(*) FROM judged WHERE stage <> 'HARD_RULES')::int          AS text_rule_pairs,
  (SELECT count(*) FROM judged WHERE NOT pass)::int                       AS text_rule_rejected,
  (SELECT count(*) FROM judged WHERE stage = 'UNVERIFIED')::int           AS unverified_pairs,
  (SELECT count(*) FROM ranked WHERE rank <= ${TOP_K})::int               AS after_top_k,
  (SELECT count(DISTINCT user_id) FROM ranked WHERE rank <= ${TOP_K})::int AS matched_users,
  (SELECT count(*) FROM ins)::int                                         AS matches_inserted;
`;

return [{
  json: {
    batch_id: batchId,
    products_planned: judged.length,
    text_rule_products: products.filter((p) => p.extra_criteria_text).length,
    sql_chars: sql.length,
    sql,
  },
}];
