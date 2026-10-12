"""GET /status/{batch_id}: progress for the upload page to poll.

Reports ingestion counts from upload_batches plus match and email counts, so
one endpoint covers the whole pipeline: uploading -> ingesting -> matching -> emailed.
"""

from __future__ import annotations

import logging
import uuid

from src.lib import db
from src.lib.http import configure_logging, json_response

configure_logging()
log = logging.getLogger(__name__)

STATUS_SQL = """
SELECT b.batch_id, b.status, b.original_name, b.total_rows, b.valid_rows,
       b.invalid_rows, b.error_message, b.created_at, b.completed_at,
       COUNT(m.match_id)                               AS matches,
       COUNT(DISTINCT m.user_id)                       AS matched_users,
       COUNT(m.match_id) FILTER (WHERE m.notified)     AS notified_matches,
       (SELECT COUNT(*) FROM email_outbox o
         WHERE o.batch_id = b.batch_id AND o.status = 'SENT')    AS emails_sent,
       (SELECT COUNT(*) FROM email_outbox o
         WHERE o.batch_id = b.batch_id AND o.status = 'DRY_RUN') AS emails_dry_run,
       (SELECT COUNT(*) FROM email_outbox o
         WHERE o.batch_id = b.batch_id AND o.status = 'FAILED')  AS emails_failed
  FROM upload_batches b
  LEFT JOIN matches m ON m.batch_id = b.batch_id
 WHERE b.batch_id = %s
 GROUP BY b.batch_id
"""


def handler(event: dict, context: object) -> dict:
    raw_id = (event.get("pathParameters") or {}).get("batch_id", "")
    try:
        batch_id = str(uuid.UUID(raw_id))
    except ValueError:
        return json_response(400, {"error": "batch_id must be a UUID."})

    with db.transaction() as cur:
        cur.execute(STATUS_SQL, (batch_id,))
        row = cur.fetchone()
        columns = [c.name for c in cur.description]

    if row is None:
        return json_response(404, {"error": f"No upload found with id {batch_id}."})
    return json_response(200, dict(zip(columns, row)))
