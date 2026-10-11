// Stage 2a: compile free-text product rules into structured checks.
//
// Input: products whose extra_criteria_text changed since it was last compiled
// (usually none, so no LLM call is made). All of them go into ONE Gemini request:
// the LLM translates rules once per product, never once per user.

const MODEL = 'gemini-2.5-flash';

const products = $input.all().map((i) => i.json).filter((p) => p.product_id);
if (products.length === 0) {
  return [{ json: { count: 0 } }];
}

const EMPLOYMENT = ['salaried', 'self_employed', 'business'];
const UNKNOWN_DATA = [
  'employer_category', 'city', 'business_vintage', 'business_turnover',
  'professional_qualification', 'tax_returns', 'other',
];

// Gemini structured output: the response must match this schema exactly.
const requirement = {
  type: 'OBJECT',
  properties: {
    credit_score_min: { type: 'INTEGER', nullable: true },
    monthly_income_min: { type: 'NUMBER', nullable: true },
    age_min: { type: 'INTEGER', nullable: true },
    age_max: { type: 'INTEGER', nullable: true },
    employment_in: { type: 'ARRAY', items: { type: 'STRING', enum: EMPLOYMENT }, nullable: true },
  },
};
const responseSchema = {
  type: 'ARRAY',
  items: {
    type: 'OBJECT',
    properties: {
      product_id: { type: 'INTEGER' },
      kind: { type: 'STRING', enum: ['eligibility', 'informational'] },
      require: requirement,
      conditions: {
        type: 'ARRAY',
        items: {
          type: 'OBJECT',
          properties: {
            when_employment_in: { type: 'ARRAY', items: { type: 'STRING', enum: EMPLOYMENT } },
            require: requirement,
          },
          required: ['when_employment_in', 'require'],
        },
      },
      needs_unknown_data: { type: 'ARRAY', items: { type: 'STRING', enum: UNKNOWN_DATA } },
      verification_note: { type: 'STRING' },
    },
    required: ['product_id', 'kind', 'require', 'conditions', 'needs_unknown_data', 'verification_note'],
  },
};

const instructions = `You translate the free-text eligibility rules of Indian loan products into machine-checkable JSON.

The ONLY applicant facts available are:
- monthly_income (INR per month)
- credit_score (300-900)
- age (years)
- employment_status: one of salaried, self_employed, business

For each product return one object:
- product_id: copied from the input.
- kind: "eligibility" if the text can exclude an applicant; "informational" if it only describes pricing, loan limits or preferences and never excludes anyone.
- require: requirements for every applicant, using only these fields: credit_score_min, monthly_income_min, age_min, age_max, employment_in. Use null for fields the text does not set.
- conditions: requirements that apply only to some employment types, as [{"when_employment_in": [...], "require": {...}}].
- needs_unknown_data: facts the rule depends on that are NOT in the applicant data (e.g. employer type, city, years in business, turnover, qualification, tax returns). Empty if none.
- verification_note: one short sentence for the applicant saying what the lender will verify, or "" if nothing.

Rules:
- Never invent thresholds that the text does not state.
- "Age at loan maturity must not exceed X" with a minimum tenure of T years means age_max = X - T.
- A threshold that depends on an unknown fact (e.g. a higher income needed in metro cities) must not go into require; list the fact in needs_unknown_data instead.
- Amounts like "Rs 25,000", "25k", "1 lakh" (= 100000) are INR.

Products:
${JSON.stringify(products.map((p) => ({
  product_id: p.product_id,
  product_name: p.product_name,
  rule_text: p.extra_criteria_text,
})), null, 2)}`;

return [{
  json: {
    count: products.length,
    model: MODEL,
    // Remember which text each compile result belongs to (md5 from SQL).
    text_hashes: Object.fromEntries(products.map((p) => [p.product_id, p.text_hash])),
    request_body: {
      contents: [{ role: 'user', parts: [{ text: instructions }] }],
      generationConfig: {
        temperature: 0,
        responseMimeType: 'application/json',
        responseSchema,
      },
    },
  },
}];
