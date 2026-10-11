-- Fallback loan products for development and demos.
--
-- ILLUSTRATIVE DATA: modelled on the ranges Indian lenders typically advertise
-- for personal and business loans, NOT verified current terms of any lender.
-- Stored with source_site = 'seed' so they are never mistaken for crawled data.
--
-- The mix is deliberate, to exercise every stage of the matching funnel:
--   * lenient products (score 650+) that most users clear outright
--   * strict products (score 750+, high income) that SQL eliminates for most users
--   * employment-restricted products (salaried-only, business-only)
--   * extra_criteria_text holding rules SQL columns cannot express, so they
--     go to the LLM stage: conditional thresholds, age-at-maturity, employer
--     category, business vintage, professional qualification
--
-- Idempotent: re-running updates the rows in place.
--   docker exec -i loan-engine-db psql -U loans -d loans < sql/seed/seed_products.sql

INSERT INTO loan_products (
  source_site, product_name, provider,
  interest_rate_min, interest_rate_max, loan_amount_min, loan_amount_max,
  min_monthly_income, min_credit_score, max_credit_score, min_age, max_age,
  allowed_employment, extra_criteria_text, source_url
) VALUES
-- Salaried, bank personal loans ------------------------------------------------
('seed', 'HDFC Bank Personal Loan', 'HDFC Bank',
  10.90, 24.00, 50000, 4000000, 25000, 720, NULL, 21, 60,
  '{salaried}', NULL, 'https://www.hdfcbank.com/personal/borrow/popular-loans/personal-loan'),
('seed', 'SBI Xpress Credit', 'State Bank of India',
  10.30, 15.30, 25000, 3500000, 15000, 700, NULL, 21, 58,
  '{salaried}', 'Only for employees of central or state government, PSUs, defence services and select reputed corporates.',
  'https://sbi.co.in/web/personal-banking/loans/personal-loans/xpress-credit'),
('seed', 'ICICI Bank Personal Loan', 'ICICI Bank',
  10.85, 16.65, 50000, 5000000, 30000, 725, NULL, 23, 58,
  '{salaried}', NULL, 'https://www.icicibank.com/personal-banking/loans/personal-loan'),
('seed', 'Axis Bank Personal Loan', 'Axis Bank',
  11.25, 22.00, 50000, 4000000, 25000, 720, NULL, 21, 60,
  '{salaried}', 'Age at loan maturity must not exceed 60 years; tenure is 1 to 5 years.',
  'https://www.axisbank.com/retail/loans/personal-loan'),
('seed', 'Kotak Mahindra Personal Loan', 'Kotak Mahindra Bank',
  10.99, 24.00, 50000, 4000000, 30000, 730, NULL, 21, 58,
  '{salaried}', NULL, 'https://www.kotak.com/en/personal-banking/loans/personal-loan.html'),
('seed', 'HDFC Bank Premium Personal Loan', 'HDFC Bank',
  10.50, 14.00, 500000, 7500000, 100000, 780, NULL, 25, 58,
  '{salaried}', 'Preferential pricing for employees of top 1000 listed companies and MNCs.',
  'https://www.hdfcbank.com/personal/borrow/popular-loans/personal-loan'),

-- NBFC and fintech personal loans -----------------------------------------------
('seed', 'Bajaj Finserv Personal Loan', 'Bajaj Finserv',
  11.00, 35.00, 40000, 5500000, 25000, 685, NULL, 21, 80,
  '{salaried}', 'Minimum net monthly salary of Rs 35,000 for applicants in Mumbai, Delhi NCR and Bengaluru; Rs 25,000 elsewhere.',
  'https://www.bajajfinserv.in/personal-loan'),
('seed', 'Tata Capital Personal Loan', 'Tata Capital',
  10.99, 29.00, 75000, 3500000, 15000, 750, NULL, 22, 58,
  '{salaried}', NULL, 'https://www.tatacapital.com/personal-loan.html'),
('seed', 'IDFC FIRST Bank Personal Loan', 'IDFC FIRST Bank',
  10.99, 23.99, 20000, 10000000, 20000, 700, NULL, 23, 60,
  '{salaried,self_employed}', 'Self-employed applicants need a credit score of at least 750; salaried applicants 700.',
  'https://www.idfcfirstbank.com/personal-banking/loans/personal-loan'),
('seed', 'Poonawalla Fincorp Instant Personal Loan', 'Poonawalla Fincorp',
  9.99, 24.00, 100000, 3000000, 25000, 750, NULL, 22, 58,
  '{salaried}', NULL, 'https://poonawallafincorp.com/personal-loan.php'),
('seed', 'Moneyview Personal Loan', 'Moneyview',
  14.00, 36.00, 5000, 1000000, 13500, 650, NULL, 21, 57,
  '{salaried,self_employed}', NULL, 'https://moneyview.in/personal-loan'),
('seed', 'KreditBee Personal Loan', 'KreditBee',
  16.00, 29.95, 6000, 500000, 15000, 650, NULL, 21, 60,
  '{salaried,self_employed}', 'Applicants earning below Rs 25,000 a month are limited to loans up to Rs 1 lakh.',
  'https://www.kreditbee.in/personal-loan'),
('seed', 'Navi Personal Loan', 'Navi',
  9.90, 45.00, 10000, 2000000, 15000, 650, NULL, 21, 65,
  '{salaried,self_employed,business}', NULL, 'https://navi.com/personal-loan'),
('seed', 'IndusInd Bank Personal Loan', 'IndusInd Bank',
  10.49, 26.00, 30000, 5000000, 25000, 700, NULL, 21, 60,
  '{salaried,self_employed}', 'Self-employed applicants must show at least 3 years of continuous income with ITR.',
  'https://www.indusind.com/in/en/personal/loans/personal-loan.html'),

-- Self-employed professionals ---------------------------------------------------
('seed', 'ICICI Bank Personal Loan for Self-Employed', 'ICICI Bank',
  11.50, 18.00, 100000, 3000000, 40000, 750, NULL, 25, 65,
  '{self_employed}', NULL, 'https://www.icicibank.com/personal-banking/loans/personal-loan'),
('seed', 'Axis Bank Loan for Professionals', 'Axis Bank',
  11.50, 20.00, 100000, 5000000, 50000, 720, NULL, 25, 65,
  '{self_employed}', 'Only for practising doctors, chartered accountants, architects and company secretaries with a recognised qualification.',
  'https://www.axisbank.com/retail/loans/personal-loan'),

-- Business owners -----------------------------------------------------------------
('seed', 'Bajaj Finserv Business Loan', 'Bajaj Finserv',
  14.00, 30.00, 100000, 8000000, 50000, 685, NULL, 24, 72,
  '{business,self_employed}', 'Business must have been operating for at least 3 years, with income tax returns filed for the last 2 years.',
  'https://www.bajajfinserv.in/business-loan'),
('seed', 'HDFC Bank Business Loan', 'HDFC Bank',
  10.75, 22.50, 100000, 7500000, 60000, 700, NULL, 21, 65,
  '{business}', 'Minimum annual business turnover of Rs 40 lakh and 3 years in the current business.',
  'https://www.hdfcbank.com/sme/borrow/business-loans'),
('seed', 'Lendingkart Business Loan', 'Lendingkart',
  12.00, 27.00, 50000, 3000000, 25000, 650, NULL, 21, 65,
  '{business,self_employed}', 'Business operating for at least 6 months with a minimum monthly turnover of Rs 90,000.',
  'https://www.lendingkart.com/business-loan'),
('seed', 'SBI Simplified Small Business Loan', 'State Bank of India',
  11.15, 15.00, 1000000, 5000000, 75000, 750, NULL, 25, 65,
  '{business}', NULL, 'https://sbi.co.in/web/business/sme')
ON CONFLICT (source_site, product_name) DO UPDATE SET
  provider            = EXCLUDED.provider,
  interest_rate_min   = EXCLUDED.interest_rate_min,
  interest_rate_max   = EXCLUDED.interest_rate_max,
  loan_amount_min     = EXCLUDED.loan_amount_min,
  loan_amount_max     = EXCLUDED.loan_amount_max,
  min_monthly_income  = EXCLUDED.min_monthly_income,
  min_credit_score    = EXCLUDED.min_credit_score,
  max_credit_score    = EXCLUDED.max_credit_score,
  min_age             = EXCLUDED.min_age,
  max_age             = EXCLUDED.max_age,
  allowed_employment  = EXCLUDED.allowed_employment,
  extra_criteria_text = EXCLUDED.extra_criteria_text,
  source_url          = EXCLUDED.source_url,
  last_seen_at        = now(),
  is_active           = TRUE;
