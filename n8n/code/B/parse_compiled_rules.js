// Stage 2b: validate Gemini's compiled rules before trusting them.
//
// Every field is checked against the allowed names and ranges. A product whose
// result is malformed is stored as status "invalid" (its matches are then flagged
// UNVERIFIED rather than guessed). If the LLM call itself failed, nothing is
// stored, so the next run retries the compile.

const request = $('Build compile request').first().json;
const response = $input.first().json;

const EMPLOYMENT = new Set(['salaried', 'self_employed', 'business']);
const UNKNOWN_DATA = new Set([
  'employer_category', 'city', 'business_vintage', 'business_turnover',
  'professional_qualification', 'tax_returns', 'other',
]);
const LIMITS = {
  credit_score_min: [300, 900],
  monthly_income_min: [0, 100000000],
  age_min: [18, 100],
  age_max: [18, 100],
};

// Postgres string literal; standard_conforming_strings is on, so doubling ' is enough.
const sqlLiteral = (value) => `'${JSON.stringify(value).replace(/'/g, "''")}'`;

function cleanRequirement(raw) {
  if (raw == null) return {};
  if (typeof raw !== 'object' || Array.isArray(raw)) throw new Error('require must be an object');
  const out = {};
  for (const [key, [lo, hi]] of Object.entries(LIMITS)) {
    const v = raw[key];
    if (v == null) continue;
    if (typeof v !== 'number' || !Number.isFinite(v) || v < lo || v > hi) {
      throw new Error(`${key} out of range: ${v}`);
    }
    out[key] = v;
  }
  if (raw.employment_in != null) {
    if (!Array.isArray(raw.employment_in) || !raw.employment_in.every((e) => EMPLOYMENT.has(e))) {
      throw new Error('employment_in has unknown values');
    }
    if (raw.employment_in.length) out.employment_in = raw.employment_in;
  }
  return out;
}

function compile(raw) {
  if (!['eligibility', 'informational'].includes(raw.kind)) throw new Error(`bad kind ${raw.kind}`);
  const conditions = (raw.conditions ?? []).map((c) => {
    const when = c.when_employment_in;
    if (!Array.isArray(when) || when.length === 0 || !when.every((e) => EMPLOYMENT.has(e))) {
      throw new Error('condition has bad when_employment_in');
    }
    return { when_employment_in: when, require: cleanRequirement(c.require) };
  });
  const unknown = raw.needs_unknown_data ?? [];
  if (!Array.isArray(unknown) || !unknown.every((u) => UNKNOWN_DATA.has(u))) {
    throw new Error('needs_unknown_data has unknown values');
  }
  return {
    status: 'ok',
    kind: raw.kind,
    require: cleanRequirement(raw.require),
    conditions,
    needs_unknown_data: unknown,
    verification_note: String(raw.verification_note ?? '').slice(0, 300),
  };
}

const text = response?.candidates?.[0]?.content?.parts?.[0]?.text;
if (!text) {
  const reason = response?.error?.message ?? response?.error ?? 'no candidates in response';
  return [{ json: { llm_ok: false, error: String(reason).slice(0, 500), saved: 0, sql_payload: "'[]'" } }];
}

let results;
try {
  results = JSON.parse(text);
  if (!Array.isArray(results)) throw new Error('response is not an array');
} catch (e) {
  return [{ json: { llm_ok: false, error: `unparseable LLM output: ${e.message}`, saved: 0, sql_payload: "'[]'" } }];
}

const byId = new Map(results.map((r) => [Number(r?.product_id), r]));
const rows = [];
const problems = [];
for (const [productId, textHash] of Object.entries(request.text_hashes)) {
  const raw = byId.get(Number(productId));
  let compiled;
  try {
    if (!raw) throw new Error('missing from LLM output');
    compiled = compile(raw);
  } catch (e) {
    compiled = { status: 'invalid', reason: e.message };
    problems.push(`product ${productId}: ${e.message}`);
  }
  rows.push({ product_id: Number(productId), text_hash: textHash, compiled });
}

return [{
  json: {
    llm_ok: true,
    saved: rows.filter((r) => r.compiled.status === 'ok').length,
    invalid: problems,
    compiled_rules: rows,
    sql_payload: sqlLiteral(rows),
  },
}];
