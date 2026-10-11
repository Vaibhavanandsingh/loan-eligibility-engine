-- Pages crawled by Workflow A. Chosen because, when checked on 2026-10-12, each
-- allowed crawling in robots.txt and served its loan terms in plain HTML (no
-- JavaScript needed). Rejected at the same check: Paisabazaar and Bajaj Finserv
-- (HTTP 403 to bots), HDFC Bank (robots.txt disallows), SBI (404), IDFC FIRST
-- (HTTP 500), Axis Bank (readable but almost no figures).
--
-- To add a source: insert a row. To pause one: UPDATE crawl_sources SET enabled = FALSE.

INSERT INTO crawl_sources (source_site, url) VALUES
  ('bankbazaar', 'https://www.bankbazaar.com/personal-loan.html'),
  ('wishfin',    'https://www.wishfin.com/personal-loan'),
  ('icicibank',  'https://www.icicibank.com/personal-banking/loans/personal-loan/eligibility')
ON CONFLICT (source_site) DO NOTHING;
