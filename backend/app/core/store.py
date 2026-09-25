"""A locked, versioned single-owner aggregate, with append-only operation receipts.

JSON stores exact decimals as strings. SELECT FOR UPDATE serializes every writer,
including bot sessions and quote refreshes. No network calls occur under this lock.
"""
from contextlib import contextmanager
from copy import deepcopy
import os
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, JSON,
                        String, Boolean, DateTime, select, update, delete, text)
from .domain import empty

engine = create_engine(os.environ.get("DATABASE_URL", "sqlite:///planner.db"), pool_pre_ping=True)
metadata = MetaData()
state = Table("planner_state", metadata, Column("id", Integer, primary_key=True),
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
stock_cache = Table(
    "stock_cache", metadata,
    Column("security_id", String(80), primary_key=True),
    Column("kind", String(32), primary_key=True),
    Column("data", JSON, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)


def migrate():
    metadata.create_all(engine)
    with engine.begin() as c:
        if c.execute(select(state.c.id)).first() is None:
            c.execute(state.insert().values(id=1, schema_version=1, data=empty()))


def lock_application(c):
    """Serialize every application writer, including derived-table refreshes/imports."""
    if engine.dialect.name == "sqlite":
        # BEGIN IMMEDIATE serializes SQLite test/development writers.
        c.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        c.execute(text("SELECT pg_advisory_xact_lock(731940251)"))


def normalized(data):
    data.setdefault("credit_accounts", {})
    return data


def read():
    with engine.connect() as c:
        row = c.execute(select(state)).mappings().one()
        if row["schema_version"] != 1:
            raise RuntimeError("Unsupported schema version")
        return normalized(deepcopy(row["data"]))


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


def replace_portfolio_history(rows):
    """Replace derived snapshots; the event ledger remains their source of truth."""
    with engine.begin() as c:
        lock_application(c)
        c.execute(delete(portfolio_history))
        if rows:
            c.execute(portfolio_history.insert(), rows)


def read_portfolio_history():
    with engine.connect() as c:
        rows = c.execute(select(portfolio_history).order_by(
            portfolio_history.c.account, portfolio_history.c.date)).mappings()
        result = {}
        for row in rows:
            item = dict(row)
            item.pop("updated_at", None)
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
        yield data
        c.execute(update(state).where(state.c.id == 1).values(data=data))


if __name__ == "__main__":
    migrate()
