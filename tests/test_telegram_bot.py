"""Telegram transport boundaries that are independent of handler scenarios."""
import ast
from pathlib import Path
from unittest.mock import Mock

from app.telegram_bot.client import BackendClient


def test_backend_client_resolves_instrument_through_api():
    response = Mock()
    response.json.return_value = {"exchange": "NASDAQ", "symbol": "AAPL",
                                  "currency": "USD", "asset_class": "equity"}
    http = Mock()
    http.get.return_value = response

    result = BackendClient(http).resolve_instrument("NASDAQ", "AAPL")

    http.get.assert_called_once_with("/api/stocks/NASDAQ%3AAAPL/identity", params={})
    response.raise_for_status.assert_called_once_with()
    assert result["currency"] == "USD"


def test_telegram_package_does_not_import_server_or_worker_internals():
    package = Path(__file__).parents[1] / "backend" / "app" / "telegram_bot"
    forbidden = ("api_server", "worker", "catalog")
    violations = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            violations.extend((path.name, module) for module in modules
                              if any(part in module for part in forbidden))
    assert violations == []
