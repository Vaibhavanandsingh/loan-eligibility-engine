"""S3 ObjectCreated -> stream the CSV into Postgres -> trigger n8n matching.

Runs once per uploaded file. S3 delivers events at least once, so this must be
safe to repeat: the batch id is derived from the object key and every insert
is an upsert, so a second run produces the same rows as the first.
"""

from __future__ import annotations

import codecs
import csv
import logging
import os
import tempfile
import uuid
from urllib.parse import unquote_plus

import boto3

from src.lib import ingest
from src.lib.http import configure_logging
from src.lib.n8n_client import trigger_matching

configure_logging()
log = logging.getLogger(__name__)

_s3 = boto3.client("s3")


def batch_id_for_key(bucket: str, key: str) -> str:
    """uploads/<uuid>.csv -> <uuid>. Files dropped into the bucket some other
    way get a deterministic id, so a redelivered event maps to the same batch."""
    stem = key.rsplit("/", 1)[-1].removesuffix(".csv")
    try:
        return str(uuid.UUID(stem))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"s3://{bucket}/{key}"))


def process_object(bucket: str, key: str) -> None:
    batch_id = batch_id_for_key(bucket, key)
    log.info("batch %s: processing s3://%s/%s", batch_id, bucket, key)
    ingest.start_batch(batch_id, key)

    # Rejected rows go to a temp file, then to S3, so bad rows never pile up in memory.
    with tempfile.NamedTemporaryFile("w+", newline="", suffix=".csv", delete=True) as rejects_file:
        rejects = csv.writer(rejects_file)
        rejects.writerow(["line", "reason", "raw_row"])

        def on_reject(line_no: int, values: list[str], reason: str) -> None:
            rejects.writerow([line_no, reason, ",".join(values)])

        body = _s3.get_object(Bucket=bucket, Key=key)["Body"]
        # Decode the byte stream incrementally; utf-8-sig drops an Excel BOM if present.
        text_stream = codecs.getreader("utf-8-sig")(body)
        try:
            result = ingest.ingest_csv(text_stream, batch_id, on_reject=on_reject)
        except ingest.BatchFormatError as exc:
            # The file itself is unusable; retrying won't help, so don't raise.
            log.error("batch %s: rejected file: %s", batch_id, exc)
            ingest.set_batch_status(batch_id, "FAILED", str(exc))
            return
        except UnicodeDecodeError:
            log.error("batch %s: file is not UTF-8 text", batch_id)
            ingest.set_batch_status(batch_id, "FAILED", "File is not UTF-8 encoded text.")
            return
        finally:
            body.close()

        if result.invalid_rows:
            rejects_file.flush()
            rejected_key = f"rejected/{batch_id}.csv"
            _s3.upload_file(rejects_file.name, bucket, rejected_key)
            log.warning(
                "batch %s: %d rows rejected (details in s3://%s/%s); first: %s",
                batch_id, result.invalid_rows, bucket, rejected_key, result.sample_errors[:3],
            )

    ingest.finish_batch(batch_id, result)
    log.info(
        "batch %s: ingested %d of %d rows", batch_id, result.valid_rows, result.total_rows
    )

    if result.valid_rows == 0:
        return  # nothing to match
    if not trigger_matching(batch_id, result.valid_rows):
        # Data is safely stored; flag the batch so the webhook can be replayed.
        ingest.set_batch_status(batch_id, "WEBHOOK_PENDING")


def handler(event: dict, context: object) -> dict:
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # S3 URL-encodes keys in events
        try:
            process_object(bucket, key)
        except Exception as exc:
            batch_id = batch_id_for_key(bucket, key)
            log.exception("batch %s: unexpected failure", batch_id)
            try:
                ingest.set_batch_status(batch_id, "FAILED", f"Internal error: {exc}"[:500])
            except Exception:
                log.exception("batch %s: could not record failure", batch_id)
            # Re-raise so Lambda retries and, after retries, sends the event to the DLQ.
            raise
    return {"ok": True}
