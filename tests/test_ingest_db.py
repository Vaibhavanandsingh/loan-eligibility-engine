"""Integration tests: real Postgres from docker-compose. Skipped when it isn't running."""

import io
import uuid

import psycopg2
import pytest

from src.lib import db, ingest


@pytest.fixture(scope="module", autouse=True)
def require_db():
    try:
        db.get_connection()
    except psycopg2.OperationalError:
        pytest.skip("local Postgres not running (docker compose up -d)")


@pytest.fixture
def batch_id():
    """A fresh batch, deleted with its users afterwards."""
    bid = str(uuid.uuid4())
    ingest.start_batch(bid, f"test/{bid}.csv")
    yield bid
    with db.transaction() as cur:
        cur.execute("DELETE FROM users WHERE batch_id = %s", (bid,))
        cur.execute("DELETE FROM upload_batches WHERE batch_id = %s", (bid,))


def make_csv(prefix: str) -> str:
    return (
        "﻿User ID,Name,Email,Monthly Income,Credit Score,Employment Status,Age,extra\n"
        f"{prefix}-1,Asha,asha@example.com,50000,720,Salaried,30,x\n"
        f"{prefix}-2,Ravi,ravi@example.com,\"1,20,000\",680,Business,45,x\n"
        f"{prefix}-3,Bad Email,nope,40000,700,Salaried,30,x\n"
        "\n"
        f"{prefix}-4,Old,old@example.com,40000,700,Salaried,130,x\n"
        f"{prefix}-1,Dup,dup@example.com,1,700,Salaried,30,x\n"
    )


def count_users(batch_id: str) -> int:
    with db.transaction() as cur:
        cur.execute("SELECT count(*) FROM users WHERE batch_id = %s", (batch_id,))
        return cur.fetchone()[0]


def test_ingest_counts_and_rejects(batch_id):
    prefix = f"t{batch_id[:8]}"
    rejected = []
    result = ingest.ingest_csv(
        io.StringIO(make_csv(prefix)), batch_id,
        on_reject=lambda line, _v, reason: rejected.append((line, reason)),
    )
    ingest.finish_batch(batch_id, result)

    assert (result.total_rows, result.valid_rows, result.invalid_rows) == (5, 2, 3)
    assert [line for line, _ in rejected] == [4, 6, 7]  # blank line 5 is skipped, not counted
    assert "duplicate user_id" in rejected[-1][1]
    assert count_users(batch_id) == 2

    with db.transaction() as cur:
        cur.execute("SELECT status, valid_rows, invalid_rows FROM upload_batches WHERE batch_id = %s", (batch_id,))
        assert cur.fetchone() == ("COMPLETED", 2, 3)
        cur.execute("SELECT monthly_income, employment_status FROM users WHERE user_id = %s", (f"{prefix}-2",))
        income, employment = cur.fetchone()
        assert (int(income), employment) == (120000, "business")


def test_reingest_is_idempotent(batch_id):
    prefix = f"t{batch_id[:8]}"
    for _ in range(2):
        ingest.ingest_csv(io.StringIO(make_csv(prefix)), batch_id)
    assert count_users(batch_id) == 2


def test_small_chunks_give_same_result(batch_id):
    prefix = f"t{batch_id[:8]}"
    result = ingest.ingest_csv(io.StringIO(make_csv(prefix)), batch_id, chunk_size=1)
    assert result.valid_rows == 2
    assert count_users(batch_id) == 2


def test_missing_columns_rejects_whole_file(batch_id):
    with pytest.raises(ingest.BatchFormatError, match="credit_score"):
        ingest.ingest_csv(io.StringIO("user_id,email,monthly_income,employment_status,age\n"), batch_id)


def test_empty_file(batch_id):
    with pytest.raises(ingest.BatchFormatError, match="empty"):
        ingest.ingest_csv(io.StringIO(""), batch_id)
