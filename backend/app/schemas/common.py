import re
from typing import Generic, List, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def normalize_iso_currency(value: str) -> str:
    """
    Validates and normalizes a 3-letter ISO 4217 currency code to uppercase.
    Matches PostgreSQL check constraint: char_length(currency) = 3 AND currency = UPPER(currency).
    """
    cleaned = value.strip().upper()
    if len(cleaned) != 3 or not cleaned.isalpha() or not cleaned.isascii():
        raise ValueError("Currency must be a valid 3-letter ISO currency code (e.g., USD, EUR, INR).")
    return cleaned


def validate_and_normalize_email(value: str) -> str:
    """
    Validates email format and normalizes whitespace and casing.
    """
    cleaned = value.strip().lower()
    if not _EMAIL_REGEX.match(cleaned):
        raise ValueError("Invalid email address format.")
    return cleaned


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Standard paginated API list response wrapper.
    """
    items: List[T] = Field(..., description="Paginated slice of items")
    total: int = Field(..., ge=0, description="Total number of matching records")
    page: int = Field(..., ge=1, description="Current 1-indexed page number")
    size: int = Field(..., ge=1, description="Requested page size")
    pages: int = Field(..., ge=0, description="Total number of available pages")

