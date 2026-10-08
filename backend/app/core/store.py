"""A locked, versioned single-owner aggregate, with append-only operation receipts.

JSON stores exact decimals as strings. SELECT FOR UPDATE serializes every writer,
including bot sessions and quote refreshes. No network calls occur under this lock.
"""
from contextlib import contextmanager
from copy import deepcopy
import os
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, BigInteger, JSON,
                        String, Boolean, DateTime, select, update, delete, text)
from datetime import datetime, timezone
import hashlib
import secrets
from .domain import empty

engine = create_engine(os.environ.get("DATABASE_URL", "sqlite:///planner.db"), pool_pre_ping=True)
metadata = MetaData()
state = Table("planner_state", metadata, Column("id", Integer, primary_key=True),
              Column("schema_version", Integer, nullable=False), Column("data", JSON, nullable=False))
tenant_ledger = Table("tenant_ledger", metadata,
    Column("principal", String(64), primary_key=True),
    Column("schema_version", Integer, nullable=False), Column("data", JSON, nullable=False))
instruments = Table(
    "instrument_catalog", metadata,
    Column("exchange", String(16), primary_key=True),
    Column("symbol", String(32), primary_key=True),
    Column("name", String(300), nullable=False),
    Column("asset_class", String(16)),
    Column("currency", String(3)),
    Column("native_exchange", String(32)),
    Column("source", String(80), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
portfolio_history = Table(
    "portfolio_history", metadata,
    Column("account", String(10), primary_key=True),
    Column("date", String(10), primary_key=True),
    Column("value_sgd", String(64), nullable=False),
    Column("gross_change_sgd", String(64)),
    Column("adjusted_change_sgd", String(64)),
    Column("external_flow_sgd", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
tenant_portfolio_history = Table(
    "tenant_portfolio_history", metadata,
    Column("principal", String(64), primary_key=True),
    Column("account", String(10), primary_key=True),
    Column("date", String(10), primary_key=True),
    Column("value_sgd", String(64), nullable=False),
    Column("gross_change_sgd", String(64)),
    Column("adjusted_change_sgd", String(64)),
    Column("external_flow_sgd", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
bot_runtime = Table("bot_runtime", metadata,
    Column("id", Integer, primary_key=True), Column("data", JSON, nullable=False))
legacy_claim = Table("legacy_ledger_claim", metadata,
    Column("id", Integer, primary_key=True), Column("claimed_by", String(64)),
    Column("claimed_at", DateTime(timezone=True)))
legacy_claim_codes = Table("legacy_ledger_claim_codes", metadata,
    Column("token_hash", String(64), primary_key=True), Column("created_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False), Column("consumed_at", BigInteger))
stock_cache = Table(
    "stock_cache", metadata,
    Column("security_id", String(80), primary_key=True),
    Column("kind", String(32), primary_key=True),
    Column("data", JSON, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)
telegram_connections = Table(
    "telegram_connections", metadata,
    Column("principal", String(255), primary_key=True),
    Column("telegram_user_id", BigInteger, nullable=False, unique=True),
    Column("linked_at", BigInteger, nullable=False),
)
telegram_link_challenges = Table(
    "telegram_link_challenges", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("principal", String(255), nullable=False, index=True),
    Column("created_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("consumed_at", BigInteger),
)
telegram_user_state = Table("telegram_user_state", metadata,
    Column("principal", String(64), primary_key=True),
    Column("telegram_user_id", BigInteger, primary_key=True), Column("data", JSON, nullable=False))


def migrate():
    metadata.create_all(engine)
    with engine.begin() as c:
        if c.execute(select(state.c.id)).first() is None:
            data = empty()
            bot = data.pop("bot", {"offset": 0, "session": None})
            c.execute(state.insert().values(id=1, schema_version=1, data=data))
            c.execute(bot_runtime.insert().values(id=1, data=bot))
        elif c.execute(select(bot_runtime.c.id)).first() is None:
            # One-way additive migration: preserve existing global Telegram state
            # while removing it from the financial ledger row.
            row = c.execute(select(state).where(state.c.id == 1)).mappings().one()
            data = deepcopy(row["data"])
            bot = data.pop("bot", {"offset": 0, "session": None})
            c.execute(update(state).where(state.c.id == 1).values(data=data))
            c.execute(bot_runtime.insert().values(id=1, data=bot))
        if c.execute(select(legacy_claim.c.id)).first() is None:
            c.execute(legacy_claim.insert().values(id=1, claimed_by=None, claimed_at=None))


def lock_application(c):
    """Serialize every application writer, including derived-table refreshes/imports."""
    if engine.dialect.name == "sqlite":
        # BEGIN IMMEDIATE serializes SQLite test/development writers.
        c.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        c.execute(text("SELECT pg_advisory_xact_lock(731940251)"))


def normalized(data):
    data.setdefault("credit_accounts", {})
    data.setdefault("recurring_schedules", [])
    return data


def read():
    with engine.connect() as c:
        row = c.execute(select(state)).mappings().one()
        if row["schema_version"] != 1:
            raise RuntimeError("Unsupported schema version")
        data = normalized(deepcopy(row["data"]))
        data["bot"] = read_bot_runtime(c)
        return data


def read_bot_runtime(c=None):
    if c is not None:
        row = c.execute(select(bot_runtime.c.data).where(bot_runtime.c.id == 1)).first()
        return deepcopy(row[0]) if row else {"offset": 0, "session": None}
    with engine.connect() as conn:
        return read_bot_runtime(conn)


def write_bot_runtime(data):
    with engine.begin() as c:
        lock_application(c)
        c.execute(update(bot_runtime).where(bot_runtime.c.id == 1).values(data=deepcopy(data)))


def _require_telegram_link(c, principal, telegram_user_id):
    linked = c.execute(select(telegram_connections.c.principal).where(
        telegram_connections.c.principal == principal,
        telegram_connections.c.telegram_user_id == telegram_user_id)).first()
    if linked is None:
        raise PermissionError("Telegram account is not linked")


def read_telegram_user_state(principal, telegram_user_id):
    with engine.connect() as c:
        _require_telegram_link(c, principal, telegram_user_id)
        row = c.execute(select(telegram_user_state.c.data).where(
            telegram_user_state.c.principal == principal,
            telegram_user_state.c.telegram_user_id == telegram_user_id)).first()
        return deepcopy(row[0]) if row else {
            "session": None, "pending_reply": None, "pending_markup": None, "last_tvm": None}


def write_telegram_user_state(principal, telegram_user_id, data):
    with engine.begin() as c:
        lock_application(c)
        _require_telegram_link(c, principal, telegram_user_id)
        stmt = update(telegram_user_state).where(
            telegram_user_state.c.principal == principal,
            telegram_user_state.c.telegram_user_id == telegram_user_id).values(data=deepcopy(data))
        result = c.execute(stmt)
        if result.rowcount == 0:
            c.execute(telegram_user_state.insert().values(
                principal=principal, telegram_user_id=telegram_user_id, data=deepcopy(data)))


def patch_telegram_user_state(principal, telegram_user_id, patch):
    """Apply a per-sender state patch atomically without losing concurrent fields."""
    with engine.begin() as c:
        lock_application(c)
        _require_telegram_link(c, principal, telegram_user_id)
        row = c.execute(select(telegram_user_state.c.data).where(
            telegram_user_state.c.principal == principal,
            telegram_user_state.c.telegram_user_id == telegram_user_id).with_for_update()).first()
        data = deepcopy(row[0]) if row else {
            "session": None, "pending_reply": None, "pending_markup": None, "last_tvm": None}
        data.update(deepcopy(patch))
        if row:
            c.execute(update(telegram_user_state).where(
                telegram_user_state.c.principal == principal,
                telegram_user_state.c.telegram_user_id == telegram_user_id).values(data=data))
        else:
            c.execute(telegram_user_state.insert().values(principal=principal,
                telegram_user_id=telegram_user_id, data=data))
        return data


def _new_tenant_data(data=None):
    value = deepcopy(data) if data is not None else empty()
    value.pop("bot", None)
    value.setdefault("recurring_schedules", [])
    return normalized(value)


def read_for(principal, telegram_user_id=None):
    """Read/create a ledger owned by the verified, server-derived principal."""
    with engine.begin() as c:
        lock_application(c)
        if telegram_user_id is not None:
            _require_telegram_link(c, principal, telegram_user_id)
        row = c.execute(select(tenant_ledger).where(tenant_ledger.c.principal == principal)).mappings().first()
        if row is None:
            data = _new_tenant_data()
            c.execute(tenant_ledger.insert().values(principal=principal, schema_version=1, data=data))
            return data
        if row["schema_version"] != 1:
            raise RuntimeError("Unsupported schema version")
        return _new_tenant_data(row["data"])


def tenant_principals():
    with engine.connect() as c:
        return [row[0] for row in c.execute(select(tenant_ledger.c.principal).order_by(
            tenant_ledger.c.principal)).all()]


def read_legacy():
    """Read only the unclaimed legacy/bot ledger; claimed legacy data is unavailable."""
    with engine.connect() as c:
        claim = c.execute(select(legacy_claim).where(legacy_claim.c.id == 1)).mappings().one()
        if claim["claimed_by"] is not None:
            raise PermissionError("Legacy ledger has been claimed")
    return read()


def assert_legacy_unclaimed():
    with engine.connect() as c:
        claimed_by = c.execute(select(legacy_claim.c.claimed_by).where(legacy_claim.c.id == 1)).scalar_one()
    if claimed_by is not None:
        raise PermissionError("Legacy ledger has been claimed")


def find_instrument(exchange, symbol):
    with engine.connect() as c:
        row = c.execute(select(instruments).where(
            instruments.c.exchange == str(exchange).upper(),
            instruments.c.symbol == str(symbol).upper(),
            instruments.c.active.is_(True),
        )).mappings().first()
        return dict(row) if row else None


def catalog_count(exchange=None, source=None):
    from sqlalchemy import func
    statement = select(func.count()).select_from(instruments).where(instruments.c.active.is_(True))
    if exchange:
        statement = statement.where(instruments.c.exchange == str(exchange).upper())
    if source:
        statement = statement.where(instruments.c.source == source)
    with engine.connect() as c:
        return c.execute(statement).scalar_one()


def replace_catalog_source(source, rows):
    """Atomically replace one bulk source while preserving cached market lookups."""
    with engine.begin() as c:
        lock_application(c)
        exchanges = sorted({row["exchange"] for row in rows})
        if exchanges:
            c.execute(delete(instruments).where(instruments.c.exchange.in_(exchanges)))
        else:
            c.execute(delete(instruments).where(instruments.c.source == source))
        if rows:
            c.execute(instruments.insert(), rows)


def cache_instrument(row):
    row = dict(row)
    with engine.begin() as c:
        lock_application(c)
        c.execute(delete(instruments).where(
            instruments.c.exchange == row["exchange"], instruments.c.symbol == row["symbol"]))
        c.execute(instruments.insert().values(**row))


def replace_portfolio_history(rows, principal=None):
    """Replace derived snapshots; the event ledger remains their source of truth."""
    with engine.begin() as c:
        lock_application(c)
        table = portfolio_history if principal is None else tenant_portfolio_history
        criteria = delete(table) if principal is None else delete(table).where(table.c.principal == principal)
        c.execute(criteria)
        if rows:
            rows = [dict(row, **({} if principal is None else {"principal": principal})) for row in rows]
            c.execute(table.insert(), rows)


def read_portfolio_history(principal=None):
    table = portfolio_history if principal is None else tenant_portfolio_history
    with engine.connect() as c:
        stmt = select(table)
        if principal is not None:
            stmt = stmt.where(table.c.principal == principal)
        rows = c.execute(stmt.order_by(table.c.account, table.c.date)).mappings()
        result = {}
        for row in rows:
            item = dict(row)
            item.pop("updated_at", None)
            item.pop("principal", None)
            result.setdefault(item.pop("account"), []).append(item)
        return result


def read_stock_cache(security_id, kind):
    with engine.connect() as c:
        row = c.execute(select(stock_cache).where(
            stock_cache.c.security_id == security_id,
            stock_cache.c.kind == kind)).mappings().first()
        return dict(row) if row else None


def write_stock_cache(security_id, kind, data, fetched_at, expires_at):
    with engine.begin() as c:
        lock_application(c)
        c.execute(delete(stock_cache).where(
            stock_cache.c.security_id == security_id,
            stock_cache.c.kind == kind))
        c.execute(stock_cache.insert().values(security_id=security_id, kind=kind,
                  data=data, fetched_at=fetched_at, expires_at=expires_at))


def search_instruments(query, exchange=None, limit=10):
    pattern = str(query).strip().upper() + "%"
    statement = select(instruments).where(instruments.c.active.is_(True),
        instruments.c.symbol.ilike(pattern))
    if exchange:
        statement = statement.where(instruments.c.exchange == str(exchange).upper())
    statement = statement.order_by(instruments.c.symbol, instruments.c.exchange).limit(limit)
    with engine.connect() as c:
        return [dict(row) for row in c.execute(statement).mappings()]


@contextmanager
def transaction():
    with engine.begin() as c:
        lock_application(c)
        row = c.execute(select(state).where(state.c.id == 1).with_for_update()).mappings().one()
        if row["schema_version"] != 1:
            raise RuntimeError("Unsupported schema version")
        data = normalized(deepcopy(row["data"]))
        data["bot"] = read_bot_runtime(c)
        yield data
        bot = data.pop("bot", {"offset": 0, "session": None})
        c.execute(update(state).where(state.c.id == 1).values(data=data))
        c.execute(update(bot_runtime).where(bot_runtime.c.id == 1).values(data=bot))


@contextmanager
def tenant_transaction(principal, telegram_user_id=None):
    with engine.begin() as c:
        lock_application(c)
        if telegram_user_id is not None:
            _require_telegram_link(c, principal, telegram_user_id)
        row = c.execute(select(tenant_ledger).where(
            tenant_ledger.c.principal == principal).with_for_update()).mappings().first()
        if row is None:
            data = _new_tenant_data()
            c.execute(tenant_ledger.insert().values(principal=principal, schema_version=1, data=data))
        else:
            if row["schema_version"] != 1:
                raise RuntimeError("Unsupported schema version")
            data = _new_tenant_data(row["data"])
        yield data
        data.pop("bot", None)
        c.execute(update(tenant_ledger).where(tenant_ledger.c.principal == principal).values(data=data))


CLAIM_TTL_SECONDS = 600


def issue_legacy_claim(now=None):
    """Issue a single-use 10-minute code. Caller must be a trusted local operator."""
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with engine.begin() as c:
        lock_application(c)
        claim = c.execute(select(legacy_claim).where(legacy_claim.c.id == 1).with_for_update()).mappings().one()
        if claim["claimed_by"] is not None:
            raise ValueError("Legacy ledger has already been claimed")
        c.execute(delete(legacy_claim_codes))
        c.execute(legacy_claim_codes.insert().values(token_hash=digest, created_at=now,
            expires_at=now + CLAIM_TTL_SECONDS, consumed_at=None))
    return token


def redeem_legacy_claim(principal, token, now=None):
    """Atomically assign the legacy ledger to principal; failures are intentionally generic."""
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest() if isinstance(token, str) else ""
    with engine.begin() as c:
        lock_application(c)
        claim = c.execute(select(legacy_claim).where(legacy_claim.c.id == 1).with_for_update()).mappings().one()
        code = c.execute(select(legacy_claim_codes).where(
            legacy_claim_codes.c.token_hash == digest).with_for_update()).mappings().first()
        tenant = c.execute(select(tenant_ledger).where(
            tenant_ledger.c.principal == principal).with_for_update()).mappings().first()
        tenant_history = c.execute(select(tenant_portfolio_history.c.account).where(
            tenant_portfolio_history.c.principal == principal).limit(1)).first()
        tenant_has_data = (tenant is not None and tenant["data"] != _new_tenant_data()) or tenant_history is not None
        if (claim["claimed_by"] is not None or code is None or code["consumed_at"] is not None
                or code["expires_at"] < now or tenant_has_data):
            raise ValueError("Claim code is invalid, expired, or already used")
        legacy = c.execute(select(state).where(state.c.id == 1).with_for_update()).mappings().one()
        data = _new_tenant_data(legacy["data"])
        if tenant is None:
            c.execute(tenant_ledger.insert().values(principal=principal,
                schema_version=legacy["schema_version"], data=data))
        else:
            c.execute(update(tenant_ledger).where(tenant_ledger.c.principal == principal).values(
                schema_version=legacy["schema_version"], data=data))
        history = c.execute(select(portfolio_history)).mappings().all()
        if history:
            c.execute(tenant_portfolio_history.insert(), [dict(row, principal=principal) for row in history])
        c.execute(update(legacy_claim_codes).where(
            legacy_claim_codes.c.token_hash == digest).values(consumed_at=now))
        c.execute(update(legacy_claim).where(legacy_claim.c.id == 1).values(
            claimed_by=principal, claimed_at=datetime.now(timezone.utc)))
        c.execute(update(state).where(state.c.id == 1).values(data=_new_tenant_data()))
        c.execute(delete(portfolio_history))
    return {"status": "claimed"}


if __name__ == "__main__":
    migrate()
