-- Workflow B scopes every query to one batch.
CREATE INDEX IF NOT EXISTS idx_users_batch ON users (batch_id);

-- Stage-1 pre-filter only ever joins against active products.
CREATE INDEX IF NOT EXISTS idx_products_active
  ON loan_products (min_credit_score, min_monthly_income) WHERE is_active;

-- Workflow C reads only un-notified matches for a batch.
CREATE INDEX IF NOT EXISTS idx_matches_pending
  ON matches (batch_id, user_id) WHERE notified = FALSE;
