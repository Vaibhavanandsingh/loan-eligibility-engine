"""Calls the n8n matching webhook (Workflow B) once a batch is ingested.

Uses only the standard library so the Lambda package stays small.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


def trigger_matching(batch_id: str, valid_rows: int, *, attempts: int = 4) -> bool:
    """POST {batch_id, valid_rows} to the n8n webhook with exponential backoff.

    Returns True once n8n acknowledges (2xx). Returns False when the webhook URL
    is not configured or every attempt fails; the caller then marks the batch
    WEBHOOK_PENDING so it can be replayed. Never raises: matching is downstream
    of ingestion and must not cause the ingested batch to be reprocessed.
    """
    url = os.environ.get("N8N_WEBHOOK_URL", "").strip()
    if not url:
        log.warning("N8N_WEBHOOK_URL not set; skipping matching trigger for %s", batch_id)
        return False

    body = json.dumps({"batch_id": batch_id, "valid_rows": valid_rows}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Secret": os.environ.get("N8N_WEBHOOK_SECRET", ""),
    }

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                log.info("n8n webhook accepted batch %s (HTTP %s)", batch_id, response.status)
                return True
        except urllib.error.HTTPError as exc:
            # 4xx (bad secret, unknown path) will not fix itself on retry.
            if 400 <= exc.code < 500 and exc.code != 429:
                log.error("n8n webhook rejected batch %s: HTTP %s", batch_id, exc.code)
                return False
            log.warning("n8n webhook attempt %d/%d failed: HTTP %s", attempt, attempts, exc.code)
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("n8n webhook attempt %d/%d failed: %s", attempt, attempts, exc)
        if attempt < attempts:
            time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s
    return False
