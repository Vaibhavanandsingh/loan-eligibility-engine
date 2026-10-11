from decimal import Decimal

import pytest

from src.lib.validation import (
    RowError,
    missing_columns,
    normalise_header,
    validate_row,
)

GOOD = {
    "user_id": "b35de23e-755f-4c6a-81d8-f7dd99ce53ef",
    "name": "Dhruv Iyengar",
    "email": "Dhruv.Iyengar@Example.com",
    "monthly_income": "131000",
    "credit_score": "725",
    "employment_status": "Self-Employed",
    "age": "37",
}


def with_(**changes):
    return {**GOOD, **changes}


def test_valid_row_is_normalised():
    row = validate_row(GOOD)
    assert row.email == "dhruv.iyengar@example.com"
    assert row.monthly_income == Decimal("131000.00")
    assert row.credit_score == 725
    assert row.employment_status == "self_employed"
    assert row.age == 37


@pytest.mark.parametrize(
    "raw, expected",
    [("Salaried", "salaried"), ("Self-Employed", "self_employed"), ("self employed", "self_employed"),
     ("Business", "business"), (" BUSINESS ", "business")],
)
def test_employment_aliases(raw, expected):
    assert validate_row(with_(employment_status=raw)).employment_status == expected


@pytest.mark.parametrize("raw", ["₹1,20,000", "Rs. 120000", "120000.00", "1,20,000"])
def test_income_formats(raw):
    assert validate_row(with_(monthly_income=raw)).monthly_income == Decimal("120000.00")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"email": "not-an-email"}, "invalid email"),
        ({"email": "a@b"}, "invalid email"),
        ({"monthly_income": "-5000"}, "monthly_income out of range"),
        ({"monthly_income": "lots"}, "not a number"),
        ({"credit_score": "1000"}, "credit_score out of range"),
        ({"credit_score": "250"}, "credit_score out of range"),
        ({"credit_score": "720.5"}, "whole number"),
        ({"age": "17"}, "age out of range"),
        ({"age": "130"}, "age out of range"),
        ({"employment_status": "Astronaut"}, "unknown employment_status"),
        ({"user_id": ""}, "missing user_id"),
        ({"email": "   "}, "missing email"),
        ({"user_id": "x" * 65}, "longer than 64"),
    ],
)
def test_bad_rows_are_rejected(changes, message):
    with pytest.raises(RowError, match=message):
        validate_row(with_(**changes))


def test_name_is_optional():
    row = validate_row({k: v for k, v in GOOD.items() if k != "name"})
    assert row.name is None


def test_missing_value_from_short_row():
    # csv yields fewer values than headers -> zip drops trailing keys
    short = {k: GOOD[k] for k in ("user_id", "email", "monthly_income")}
    with pytest.raises(RowError, match="missing credit_score"):
        validate_row(short)


def test_header_normalisation():
    assert normalise_header("﻿User ID") == "user_id"
    assert normalise_header(" Monthly Income ") == "monthly_income"
    assert normalise_header("employment-status") == "employment_status"


def test_missing_columns():
    assert missing_columns(["user_id", "email", "age"]) == [
        "monthly_income", "credit_score", "employment_status",
    ]
    assert missing_columns(list(GOOD)) == []
