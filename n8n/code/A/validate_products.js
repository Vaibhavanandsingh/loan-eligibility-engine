// Validate and normalise the products Gemini extracted from each page.
//
// Out-of-range numbers are dropped (set to null), min/max pairs are put in
// order, and a product with no eligibility terms at all is discarded: with no
// rules it would match every user, which would be misleading. If nothing valid
// comes back for a page, the page is marked extract_failed and its stored hash
// is not updated, so the next run tries again.

const EMPLOYMENT = new Set(['salaried', 'self_employed', 'business']);
const RANGES = {
  interest_rate_min: [1, 60], interest_rate_max: [1, 60],
  loan_amount_min: [1000, 1e9], loan_amount_max: [1000, 1e9],
  min_monthly_income: [1000, 1e7],
  min_credit_score: [300, 900], max_credit_score: [300, 900],
  min_age: [18, 100], max_age: [18, 100],
};
const MAX_PRODUCTS = 30;

const sqlText = (value) => (value == null ? 'NULL' : `'${String(value).replace(/'/g, "''")}'`);
const sqlJson = (value) => `'${JSON.stringify(value).replace(/'/g, "''")}'`;

function inRange(value, [lo, hi]) {
  return typeof value === 'number' && Number.isFinite(value) && value >= lo && value <= hi ? value : null;
}

function ordered(p, lo, hi) {
  if (p[lo] != null && p[hi] != null && p[lo] > p[hi]) [p[lo], p[hi]] = [p[hi], p[lo]];
}

function clean(raw) {
  const name = String(raw.product_name ?? '').replace(/\s+/g, ' ').trim();
  if (name.length < 3 || name.length > 150) return null;
  const p = { product_name: name, provider: String(raw.provider ?? '').trim().slice(0, 100) || null };
  for (const [field, range] of Object.entries(RANGES)) p[field] = inRange(raw[field], range);
  ordered(p, 'interest_rate_min', 'interest_rate_max');
  ordered(p, 'loan_amount_min', 'loan_amount_max');
  ordered(p, 'min_credit_score', 'max_credit_score');
  ordered(p, 'min_age', 'max_age');
  const employment = Array.isArray(raw.allowed_employment)
    ? [...new Set(raw.allowed_employment.filter((e) => EMPLOYMENT.has(e)))]
    : [];
  p.allowed_employment = employment.length ? employment : null;
  const extra = String(raw.extra_criteria_text ?? '').trim();
  p.extra_criteria_text = extra ? extra.slice(0, 600) : null;

  const hasEligibility = p.min_monthly_income != null || p.min_credit_score != null
    || p.min_age != null || p.max_age != null || p.allowed_employment != null || p.extra_criteria_text != null;
  return hasEligibility ? p : null;
}

const requests = $('Build extract request').all().map((i) => i.json);

return $input.all().map((item, index) => {
  const req = requests[item.pairedItem?.item ?? index];
  const response = item.json;
  let products = [];
  let error = null;

  const text = response?.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!text) {
    error = String(response?.error?.message ?? response?.error ?? 'no candidates in response').slice(0, 300);
  } else {
    try {
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed)) throw new Error('response is not an array');
      const seen = new Set();
      for (const raw of parsed) {
        const p = clean(raw ?? {});
        if (!p || seen.has(p.product_name.toLowerCase())) continue;
        seen.add(p.product_name.toLowerCase());
        products.push(p);
      }
      products = products.slice(0, MAX_PRODUCTS);
      if (products.length === 0) error = 'no valid products extracted';
    } catch (e) {
      error = `unparseable LLM output: ${e.message}`;
    }
  }

  const ok = products.length > 0;
  return {
    json: {
      source_site: req.source_site,
      ok,
      product_count: products.length,
      error,
      products_sql: sqlJson(products),
      q_site: sqlText(req.source_site),
      q_url: sqlText(req.url),
      q_hash: sqlText(req.text_hash),
      q_status: sqlText(ok ? 'extracted' : 'extract_failed'),
      q_error: sqlText(error),
      q_ok: ok ? 'TRUE' : 'FALSE',
    },
  };
});
