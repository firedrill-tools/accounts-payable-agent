"""Bounded invoice facts and exact source-quoted money conversion."""

from datetime import date
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from types import MappingProxyType
from pydantic import BaseModel, ConfigDict, Field, field_validator

SAFE_INTEGER = 2**53 - 1
MAX_TEXT = 100000
EXPONENTS = MappingProxyType(
    {"USD": 2, "CAD": 2, "EUR": 2, "GBP": 2, "JPY": 0, "KWD": 3}
)
DASHES = str.maketrans(
    {character: "-" for character in "\u2010\u2011\u2012\u2013\u2014\u2212"}
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def identity(value):
    return " ".join(
        unicodedata.normalize("NFKC", value).translate(DASHES).casefold().split()
    )


def identifier(value, name="identifier"):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > 100
    ):
        raise ValueError(
            f"{name} requires 1–100 nonblank characters without surrounding whitespace"
        )
    return value


def row_id(value):
    if type(value) is not int or not 1 <= value <= SAFE_INTEGER:
        raise ValueError("invoice ID must be a positive safe integer")
    return value


def amount(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= SAFE_INTEGER:
        raise ValueError("amount must be a strict safe integer in minor units")
    return value


def currency(value):
    if not isinstance(value, str) or value not in EXPONENTS:
        raise ValueError("supported currencies are USD, CAD, EUR, GBP, JPY and KWD")
    return value


def parse_amount(text, code):
    currency(code)
    if (
        not isinstance(text, str)
        or len(text) > 100
        or not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text)
    ):
        raise ValueError(
            "total must use an unambiguous nonnegative decimal amount with valid optional comma grouping"
        )
    numerator, denominator = Decimal(text.replace(",", "")).as_integer_ratio()
    scaled = numerator * 10 ** EXPONENTS[code]
    if scaled % denominator:
        raise ValueError("total contains a fractional minor unit")
    return amount(scaled // denominator)


class InvoiceFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vendor_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=100)
    po_number: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    total_minor: int | None = Field(default=None, ge=0, le=SAFE_INTEGER, strict=True)
    total_text: str | None = Field(default=None, max_length=100)
    due_date: str | None = None
    concerns: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator(
        "vendor_name",
        "invoice_number",
        "po_number",
        "currency",
        "total_text",
        "due_date",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("due_date")
    @classmethod
    def iso_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("due_date must be ISO YYYY-MM-DD")
        return value

    @field_validator("concerns")
    @classmethod
    def real_concerns(cls, value):
        if any(not item.strip() or len(item) > 1000 for item in value):
            raise ValueError("concerns must be substantive and bounded")
        return value


class ExtractedInvoice(BaseModel):
    """The model quotes the amount; application code performs exact conversion."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    vendor_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=100)
    po_number: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    total_text: str | None = Field(
        default=None,
        max_length=100,
        description="Exact numeric total as printed, including any comma grouping; no currency symbol or guessed value",
    )
    due_date: str | None = None
    concerns: tuple[str, ...] = Field(default=(), max_length=12)

    @field_validator("due_date")
    @classmethod
    def iso_date(cls, value):
        return InvoiceFacts.iso_date(value)

    @field_validator("concerns")
    @classmethod
    def substantive(cls, value):
        return InvoiceFacts.real_concerns(value)


def source_contains(text, value, *, numeric=False):
    text = identity(text)
    value = identity(value)
    boundary = r"[0-9.,]" if numeric else r"[\w/\-]"
    return (
        re.search(
            r"(?<!" + boundary + r")" + re.escape(value) + r"(?!" + boundary + r")",
            text,
        )
        is not None
    )


def to_facts(raw, text):
    raw = ExtractedInvoice.model_validate(raw)
    data = raw.model_dump()
    concerns = list(raw.concerns)
    total = None
    for key in ("vendor_name", "invoice_number", "po_number", "currency", "due_date"):
        value = data[key]
        if value and not source_contains(text, value):
            concerns.append(f"{key} is not supported by an exact source phrase")
            data[key] = None
    if raw.total_text:
        if not source_contains(text, raw.total_text, numeric=True):
            concerns.append("total is not supported by an exact source phrase")
        else:
            try:
                total = parse_amount(raw.total_text, data["currency"])
            except ValueError as error:
                concerns.append(str(error))
    else:
        concerns.append("missing printed total")
    data.update(total_minor=total, concerns=tuple(dict.fromkeys(concerns)))
    return InvoiceFacts.model_validate(data)


def bounded_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("invoice text must contain 1–100000 characters")
    return text
