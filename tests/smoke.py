"""Run only against the disposable financial-planner-test Compose project.

Creates synthetic accounts; never run this against a personal database.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import uuid
import httpx

base = "http://web:8080"
bot = {"Authorization": "Bearer " + os.environ["BOT_API_SECRET"]}


def command(kind, payload, key=None):
    response = httpx.post(base + "/api/commands/" + kind, json=payload,
                          headers={**bot, "Idempotency-Key": key or uuid.uuid4().hex}, timeout=30)
    response.raise_for_status()
    return response.json()


assert httpx.get(base + "/api/dashboard").status_code == 401
initial = httpx.get(base + "/api/dashboard", headers=bot).json()
assert not initial["accounts"], "Smoke tests require an empty disposable database"
today = initial["as_of"]
bank = command("account_add", {"name": "Example · Everyday account", "type": "bank", "currency": "SGD"})["id"]
broker = command("account_add", {"name": "Example · Brokerage", "type": "brokerage", "currency": "USD"})["id"]
cpf = command("account_add", {"name": "Example · CPF Ordinary Account", "type": "cpf", "currency": "SGD", "cpf_type": "OA"})["id"]
command("opening_cash", {"account": bank, "currency": "SGD", "amount": "24500", "date": "2026-01-01"})
command("opening_cash", {"account": broker, "currency": "USD", "amount": "10000", "date": "2026-01-01"})
command("opening_cash", {"account": cpf, "currency": "SGD", "amount": "68000", "date": "2026-01-01"})
for symbol, asset_class, qty, price in [("AAPL", "equity", "25", "200"), ("VOO", "etf", "30", "550")]:
    command("opening_holding", {"account": broker, "exchange": "NASDAQ" if symbol == "AAPL" else "NYSE",
                                 "symbol": symbol, "asset_class": asset_class, "quantity": qty,
                                 "price": price, "currency": "USD", "date": "2026-01-01"})
deposit = {"account": bank, "currency": "SGD", "amount": "1", "date": today}
with ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(lambda _: command("deposit", deposit), range(24)))
with ThreadPoolExecutor(max_workers=8) as pool:
    duplicates = list(pool.map(lambda _: command("deposit", deposit, "duplicate-smoke-key"), range(8)))
assert len({e["id"] for e in duplicates}) == 1
before = httpx.get(base + "/api/dashboard", headers=bot).json()
assert next(a for a in before["accounts"] if a["id"] == bank)["cash"][0]["amount"] == "24525"
rejected = httpx.post(base + "/api/commands/withdraw", headers={**bot, "Idempotency-Key": "invalid-withdraw"},
                     json={**deposit, "amount": "999999999"})
assert rejected.status_code == 422
assert len(httpx.get(base + "/api/dashboard", headers=bot).json()["history"]) == len(before["history"])

web = httpx.Client(base_url=base, headers={"Origin": "http://localhost:18080"}, timeout=30)
login = web.post("/api/login", json={"password": "local-test-password"})
login.raise_for_status()
web.headers["X-CSRF-Token"] = login.json()["csrf"]


def web_command(kind, payload):
    response = web.post("/api/commands/" + kind, json=payload, headers={"Idempotency-Key": uuid.uuid4().hex})
    response.raise_for_status()
    return response.json()


loan = web_command("loan_add", {"name": "Example · HDB housing loan", "principal": "250000", "opening_interest": "0",
                    "rate": "2.6", "installment": "1800", "as_of": "2026-08-31", "next_due": "2026-09-15"})
payment = web_command("repayment", {"loan": loan["id"], "date": "2026-09-15", "amount": "1800",
                        "allocations": [{"account": bank, "amount": "500"}, {"account": cpf, "amount": "1300"}]})
view = web.get("/api/dashboard").json()
assert Decimal(view["loans"][0]["outstanding_principal"]) == Decimal("248741.67")
assert Decimal(view["loans"][0]["accrued_interest"]) == 0
bad = httpx.post(base + "/api/commands/void", headers={**bot, "Idempotency-Key": "bot-cannot-void"}, json={"transaction": payment["id"]})
assert bad.status_code == 422
assert web.get("/").status_code == 200
assert web.get("/app.js").status_code == 200
assert "frame-ancestors 'none'" in web.get("/").headers["content-security-policy"]
print("PASS: Compose routing, authentication/CSRF, PostgreSQL concurrent writes, deduplication, rollback, loan split funding, bot permissions, static assets.")
