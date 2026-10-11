"""PostgreSQL connection helpers.

The connection is cached at module level so warm Lambda invocations reuse it
instead of paying the TLS + auth handshake on every event.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extensions

_conn: psycopg2.extensions.connection | None = None


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        # RDS enforces TLS (rds.force_ssl); local Docker Postgres sets DB_SSLMODE=disable.
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        connect_timeout=10,
        application_name=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "loan-engine-local"),
    )


def get_connection() -> psycopg2.extensions.connection:
    """Return a live connection, reconnecting if the cached one was dropped."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = _connect()
    else:
        try:
            with _conn.cursor() as cur:
                cur.execute("SELECT 1")
            _conn.rollback()
        except psycopg2.Error:
            _conn = _connect()
    return _conn


@contextmanager
def transaction() -> Iterator[psycopg2.extensions.cursor]:
    """Yield a cursor inside a transaction: commit on success, roll back on error."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
