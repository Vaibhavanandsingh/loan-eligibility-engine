"""PostgreSQL connection helpers.

The connection is cached so warm Lambda invocations reuse it instead of paying
the TLS + auth handshake on every event. The cache is per thread: Lambda runs
one thread per container, and the local dev server (scripts/dev_server.py)
ingests in one thread while serving status polls in others.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extensions

_local = threading.local()


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
    """Return this thread's live connection, reconnecting if it was dropped."""
    conn: psycopg2.extensions.connection | None = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        conn = _connect()
    else:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.rollback()
        except psycopg2.Error:
            conn = _connect()
    _local.conn = conn
    return conn


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
