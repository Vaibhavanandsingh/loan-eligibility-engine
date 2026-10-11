// One Gemini request per changed page: extract its loan products as JSON.

const MODEL = 'gemini-2.5-flash';
const EMPLOYMENT = ['salaried', 'self_employed', 'business'];

const num = (type = 'NUMBER') => ({ type, nullable: true });
const responseSchema = {
  type: 'ARRAY',
  items: {
    type: 'OBJECT',
    properties: {
      product_name: { type: 'STRING' },
      provider: { type: 'STRING' },
      interest_rate_min: num(),
      interest_rate_max: num(),
      loan_amount_min: num(),
      loan_amount_max: num(),
      min_monthly_income: num(),
      min_credit_score: num('INTEGER'),
      max_credit_score: num('INTEGER'),
      min_age: num('INTEGER'),
      max_age: num('INTEGER'),
      allowed_employment: { type: 'ARRAY', items: { type: 'STRING', enum: EMPLOYMENT }, nullable: true },
      extra_criteria_text: { type: 'STRING', nullable: true },
    },
    required: ['product_name', 'provider'],
  },
};

function instructions(page) {
  return `You extract loan products from the text of an Indian financial web page (a bank or a loan comparison site).

Return one object per distinct personal or business loan product whose terms the page states:
- product_name: lender plus product, e.g. "Yes Bank Personal Loan".
- provider: the lender's name.
- interest_rate_min / interest_rate_max: percent per year.
- loan_amount_min / loan_amount_max, min_monthly_income: INR. Convert "₹15,000" to 15000, "1 lakh" to 100000, "40L" to 4000000, "1 crore" to 10000000.
- min_credit_score / max_credit_score: CIBIL-style 300-900.
- min_age / max_age: years.
- allowed_employment: subset of salaried, self_employed, business; null if the page does not say.
- extra_criteria_text: eligibility rules the fields above cannot hold, as short plain sentences; null if none.

Rules:
- Use only values the page states for that specific product. Do not copy general advice that the page gives about loans in general onto a lender.
- When a rule differs by group (e.g. salaried age 21-60, self-employed 21-65), put the most permissive value in the field (min_age 21, max_age 65) and state the group-specific limits in extra_criteria_text ("Salaried applicants: maximum age 60.").
- Rules about city, employer type, work experience, years in business, turnover or tax returns go in extra_criteria_text.
- Skip a product if the page gives no eligibility terms for it at all (no income, credit score, age or employment rule).
- Return at most 30 products. Return [] if there are none.

Page: ${page.source_site} (${page.url})
---
${page.text}`;
}

return $input.all().map(({ json: page }) => ({
  json: {
    source_site: page.source_site,
    url: page.url,
    text_hash: page.text_hash,
    model: MODEL,
    request_body: {
      contents: [{ role: 'user', parts: [{ text: instructions(page) }] }],
      generationConfig: { temperature: 0, responseMimeType: 'application/json', responseSchema },
    },
  },
}));
