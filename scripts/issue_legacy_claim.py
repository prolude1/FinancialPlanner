"""Issue a one-use legacy-ledger claim code from a trusted local operator shell."""
from pathlib import Path
import os
import sys

project_root = Path(__file__).resolve().parents[1]
backend_root = project_root / "backend"
sys.path.insert(0, str(backend_root if (backend_root / "app").is_dir() else project_root))
from app.core.store import issue_legacy_claim


if __name__ == "__main__":
    # Print once for immediate out-of-band delivery. Never pass the code on the
    # command line or put it in a URL, environment variable, or log.
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL must be set to the intended application database")
    print(issue_legacy_claim())
