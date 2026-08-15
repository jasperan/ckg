"""Database utilities for the sample project."""

from sample_pkg.utils.helpers import sanitize_input


class DatabaseConnection:
    """A mock database connection."""

    def __init__(self, dsn: str = "localhost:1521/FREEPDB1"):
        self.dsn = dsn
        self.connected = False

    def open(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected


def connect_db() -> DatabaseConnection:
    """Create and open a database connection."""
    conn = DatabaseConnection()
    conn.open()
    return conn


def query_db(conn: DatabaseConnection, query: str) -> list:
    """Execute a query with sanitized input."""
    safe_query = sanitize_input(query)
    if not conn.is_connected():
        raise RuntimeError("Database not connected")
    return [f"executed: {safe_query}"]


def migrate_schema(conn: DatabaseConnection) -> None:
    """Run schema migrations."""
    query_db(conn, "CREATE TABLE orders (id INT, email TEXT, total TEXT)")
# add a comment
# another change

# updated with core.py

# touched with helpers.py

# updated with core.py

# touched with helpers.py

# updated with core.py

# touched with helpers.py
