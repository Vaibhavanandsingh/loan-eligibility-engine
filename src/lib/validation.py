"""Row-level validation and normalisation for uploaded user CSVs.

Pure functions with no I/O, so they are cheap to unit test and reuse from
both the Lambda handler and the local ingestion script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

REQUIRED_COLUMNS: tuple[str, ...] = (
    "user_id",
    "email",
    "monthly_income",
    "credit_score",
    "employment_status",
    "age",
)
OPTIONAL_COLUMNS: tuple[str, ...] = ("name",)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Free-text employment values seen in the wild -> canonical enum stored in the DB.
EMPLOYMENT_ALIASES: dict[str, str] = {
    "salaried": "salaried",
    "salary": "salaried",
    "employed": "salaried",
    "full_time": "salaried",
    "self_employed": "self_employed",
    "selfemployed": "self_employed",
    "freelancer": "self_employed",
    "self_employed_professional": "self_employed",
    "business": "business",
    "business_owner": "business",
    "self_employed_business": "business",
    "unemployed": "unemployed",
    "other": "other",
}

MAX_INCOME = Decimal("100000000")  # 10 crore / month: anything above is a data error


@dataclass(frozen=True, slots=True)
class UserRow:
    """A validated, normalised user record ready for insertion."""

    user_id: str
    name: str | None
    email: str
    monthly_income: Decimal
    credit_score: int
    employment_status: str
    age: int

    def as_tuple(self) -> tuple:
        return (
            self.user_id,
            self.name,
            self.email,
            self.monthly_income,
            self.credit_score,
            self.employment_status,
            self.age,
        )


class RowError(ValueError):
    """Raised when a row cannot be accepted. The message is user-facing."""


def normalise_header(header: str) -> str:
    """'Monthly Income ' -> 'monthly_income'; also strips a UTF-8 BOM."""
    return re.sub(r"[\s\-]+", "_", header.replace("﻿", "").strip().lower())


def missing_columns(headers: list[str]) -> list[str]:
    """Return required columns absent from an already-normalised header row."""
    present = set(headers)
    return [c for c in REQUIRED_COLUMNS if c not in present]


def normalise_employment(value: str) -> str:
    key = normalise_header(value)
    if key not in EMPLOYMENT_ALIASES:
        raise RowError(f"unknown employment_status '{value}'")
    return EMPLOYMENT_ALIASES[key]


def _parse_decimal(value: str, field: str) -> Decimal:
    # Accept '₹1,20,000', 'Rs. 45000', '45000.50'.
    cleaned = re.sub(r"(?i)^(rs\.?|inr|₹)\s*", "", value.strip()).replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise RowError(f"{field} is not a number: '{value}'") from None


def _parse_int(value: str, field: str) -> int:
    number = _parse_decimal(value, field)
    if number != number.to_integral_value():
        raise RowError(f"{field} must be a whole number: '{value}'")
    return int(number)


def validate_row(raw: Mapping[str, str | None]) -> UserRow:
    """Validate one CSV row (keys already normalised). Raises RowError."""
    row = {k: (v or "").strip() for k, v in raw.items() if k}

    for col in REQUIRED_COLUMNS:
        if not row.get(col):
            raise RowError(f"missing {col}")

    user_id = row["user_id"]
    if len(user_id) > 64:
        raise RowError("user_id longer than 64 characters")

    email = row["email"].lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise RowError(f"invalid email '{row['email']}'")

    income = _parse_decimal(row["monthly_income"], "monthly_income")
    if income < 0 or income > MAX_INCOME:
        raise RowError(f"monthly_income out of range: {income}")

    credit_score = _parse_int(row["credit_score"], "credit_score")
    if not 300 <= credit_score <= 900:
        raise RowError(f"credit_score out of range 300-900: {credit_score}")

    age = _parse_int(row["age"], "age")
    if not 18 <= age <= 100:
        raise RowError(f"age out of range 18-100: {age}")

    name = row.get("name") or None
    if name and len(name) > 200:
        name = name[:200]

    return UserRow(
        user_id=user_id,
        name=name,
        email=email,
        monthly_income=income.quantize(Decimal("0.01")),
        credit_score=credit_score,
        employment_status=normalise_employment(row["employment_status"]),
        age=age,
    )
