"""Keep process-specific packages from growing hidden cross-dependencies."""
import ast
from pathlib import Path


APP = Path(__file__).parents[1] / "backend" / "app"


def imports_under(package):
    for path in (APP / package).rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                yield path, *(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = "." * node.level + module
                yield path, module


def test_core_does_not_import_process_packages():
    forbidden = ("app.api_server", "app.telegram_bot", "..api_server", "..telegram_bot")
    violations = [(str(path), module) for path, *modules in imports_under("core")
                  for module in modules if module.startswith(forbidden)]
    assert violations == []


def test_api_does_not_import_telegram_package():
    violations = [(str(path), module) for path, *modules in imports_under("api_server")
                  for module in modules if "telegram_bot" in module]
    assert violations == []


def test_redundant_flat_module_aliases_are_removed():
    aliases = ("api", "bot", "catalog", "domain", "finance", "providers", "schemas",
               "stocks", "store", "views", "worker")
    assert [str(APP / f"{name}.py") for name in aliases if (APP / f"{name}.py").exists()] == []
