"""Global test isolation guard.

API fixtures clear the aggregate deliberately. Refuse collection when a caller
accidentally carries the personal deployment database into pytest.
"""
import os
import tempfile
from urllib.parse import urlparse


database_url = os.environ.get("DATABASE_URL", "")
if not database_url:
    os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(prefix="planner-tests-", suffix=".db")
elif not database_url.startswith("sqlite:"):
    database_name = urlparse(database_url.replace("postgresql+psycopg", "postgresql", 1)).path.lstrip("/")
    if "test" not in database_name.lower():
        raise RuntimeError(
            "Refusing to run pytest against a non-test PostgreSQL database. "
            "Set DATABASE_URL to SQLite or a database whose name contains 'test'."
        )
