"""Utility helpers for the sample project."""

import re


def validate_email(email: str) -> bool:
    """Check if an email address looks valid."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def format_price(amount: float) -> str:
    """Format a price with currency symbol."""
    return f"${amount:,.2f}"


def sanitize_input(text: str) -> str:
    """Remove potentially dangerous characters from input."""
    return re.sub(r"[<>\"']", "", text)
# another change

# changed with db.py

# changed with db.py

# changed with db.py
