"""Streaming CSV -> PostgreSQL ingestion, independent of where the bytes come from.

The Lambda handler feeds it an S3 body stream; scripts/local_ingest.py feeds it
a local file. Memory use is bounded by CHUNK_SIZE rows plus the set of seen
user_ids, regardless of file size.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, TextIO

from psycopg2.extras import execute_values

from . import db
from .validation import RowError, UserRow, missing_columns, normalise_header, validate_row

log = logging.getLogger(__name__)

CHUNK_SIZE = 2000

UPSERT_USERS_SQL = """
INSERT INTO users (user_id, name, email, monthly_income, credit_score,
                   employment_status, age, batch_id)
VALUES %s
ON CONFLICT (user_id) DO UPDATE SET
  name              = EXCLUDED.name,
  email             = EXCLUDED.email,
  monthly_income    = EXCLUDED.monthly_income,
  credit_score      = EXCLUDED.credit_score,
  employment_status = EXCLUDED.employment_status,
  age               = EXCLUDED.age,
  batch_id          = EXCLUDED.batch_id,
  updated_at        = now()
"""

RejectHandler = Callable[[int, list[str], str], None]


class BatchFormatError(Exception):
    """The file as a whole is unusable (e.g. required columns missing)."""


@dataclass
class IngestResult:
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    sample_errors: list[str] = field(default_factory=list)


def _flush(chunk: list[UserRow], batch_id: str) -> None:
    with db.transaction() as cur:
        execute_values(
            cur,
            UPSERT_USERS_SQL,
            [row.as_tuple() + (batch_id,) for row in chunk],
            page_size=len(chunk),
        )


def _update_progress(batch_id: str, result: IngestResult) -> None:
    with db.transaction() as cur:
        cur.execute(
            """UPDATE upload_batches
                  SET total_rows = %s, valid_rows = %s, invalid_rows = %s
                WHERE batch_id = %s""",
            (result.total_rows, result.valid_rows, result.invalid_rows, batch_id),
        )


def ingest_csv(
    stream: TextIO | Iterable[str],
    batch_id: str,
    *,
    on_reject: RejectHandler | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> IngestResult:
    """Validate and upsert every row of a CSV text stream under `batch_id`.

    Each chunk commits on its own, so progress is visible to the status
    endpoint while a large file is still loading. Upserts are idempotent, so
    an S3 event redelivery that re-runs the whole file is harmless.
    """
    reader = csv.reader(stream)
    try:
        headers = [normalise_header(h) for h in next(reader)]
    except StopIteration:
        raise BatchFormatError("file is empty") from None

    missing = missing_columns(headers)
    if missing:
        raise BatchFormatError(f"missing required column(s): {', '.join(missing)}")

    result = IngestResult()
    seen_ids: set[str] = set()
    chunk: list[UserRow] = []

    def reject(line_no: int, values: list[str], reason: str) -> None:
        result.invalid_rows += 1
        if len(result.sample_errors) < 20:
            result.sample_errors.append(f"line {line_no}: {reason}")
        if on_reject:
            on_reject(line_no, values, reason)

    for line_no, values in enumerate(reader, start=2):
        if not values or all(not v.strip() for v in values):
            continue  # blank line: not a data row
        result.total_rows += 1
        try:
            user = validate_row(dict(zip(headers, values)))
        except RowError as exc:
            reject(line_no, values, str(exc))
            continue
        if user.user_id in seen_ids:
            reject(line_no, values, f"duplicate user_id '{user.user_id}' in file")
            continue
        seen_ids.add(user.user_id)
        chunk.append(user)
        result.valid_rows += 1

        if len(chunk) >= chunk_size:
            _flush(chunk, batch_id)
            chunk.clear()
            _update_progress(batch_id, result)

    if chunk:
        _flush(chunk, batch_id)
    return result


def start_batch(batch_id: str, s3_key: str) -> None:
    """Mark a batch PROCESSING, creating it if the file arrived without the UI."""
    with db.transaction() as cur:
        cur.execute(
            """INSERT INTO upload_batches (batch_id, s3_key, status)
               VALUES (%s, %s, 'PROCESSING')
               ON CONFLICT (batch_id) DO UPDATE
                 SET status = 'PROCESSING', error_message = NULL,
                     total_rows = 0, valid_rows = 0, invalid_rows = 0""",
            (batch_id, s3_key),
        )


def finish_batch(batch_id: str, result: IngestResult) -> None:
    error = "; ".join(result.sample_errors[:5]) or None
    with db.transaction() as cur:
        cur.execute(
            """UPDATE upload_batches
                  SET status = 'COMPLETED', total_rows = %s, valid_rows = %s,
                      invalid_rows = %s, error_message = %s, completed_at = now()
                WHERE batch_id = %s""",
            (result.total_rows, result.valid_rows, result.invalid_rows, error, batch_id),
        )


def set_batch_status(batch_id: str, status: str, error: str | None = None) -> None:
    with db.transaction() as cur:
        cur.execute(
            """UPDATE upload_batches
                  SET status = %s,
                      error_message = COALESCE(%s, error_message),
                      completed_at = COALESCE(completed_at, now())
                WHERE batch_id = %s""",
            (status, error, batch_id),
        )
