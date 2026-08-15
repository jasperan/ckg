"""Sample package for CKG testing.

Core business logic for a fictional order-processing system.
Demonstrates cross-module imports, function calls, and co-editable files.
"""

from sample_pkg.utils.helpers import validate_email, format_price
from sample_pkg.utils.db import connect_db, query_db


def process_order(customer_email: str, items: list[dict]) -> dict:
    """Process an order from a customer.

    Args:
        customer_email: Customer's email address.
        items: List of items with 'name' and 'price' keys.

    Returns:
        Order summary dict with total and status.
    """
    if not validate_email(customer_email):
        return {"status": "error", "message": "Invalid email"}

    db = connect_db()
    total = 0.0
    for item in items:
        total += item.get("price", 0)

    formatted_total = format_price(total)
    _save_order(db, customer_email, formatted_total)

    return {"status": "ok", "total": formatted_total, "items": len(items)}


def _save_order(db, email: str, total: str) -> None:
    """Persist an order to the database."""
    query_db(db, f"INSERT INTO orders (email, total) VALUES ('{email}', '{total}')")


class OrderValidator:
    """Validates order constraints before processing."""

    MAX_ITEMS = 100
    MAX_TOTAL = 10000.0

    def validate(self, items: list[dict]) -> bool:
        """Check if an order meets constraints."""
        if len(items) > self.MAX_ITEMS:
            return False
        total = sum(item.get("price", 0) for item in items)
        return total <= self.MAX_TOTAL

    def get_remaining_capacity(self, items: list[dict]) -> int:
        """Return how many more items can be added."""
        return self.MAX_ITEMS - len(items)
# add a comment

# changed with db.py

# changed with db.py

# changed with db.py
