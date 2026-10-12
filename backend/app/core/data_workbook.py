"""Database-backed workbook export and guarded import orchestration.

XLSX encoding, decoding, and schema validation live in :mod:`workbook_codec`.
This module keeps the existing API used by routes and tests.
"""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import logging
import os
from pathlib import Path
import secrets
import tempfile
import time

from sqlalchemy import delete, insert, select, update

from . import store, workbook_codec as _codec
from .workbook_codec import (
    APP_VERSION, DESCRIPTIONS, FORMAT_VERSION, HEADERS, MAX_CELL_TEXT,
    MAX_SHEET_ROWS, MAX_UNCOMPRESSED_BYTES, MAX_UPLOAD_BYTES, MAX_WORKBOOK_BYTES,
    SHEETS, TABLES, WORKBOOK_TYPE, WorkbookError, _build_xlsx as _codec_build_xlsx,
    _db_row, _digest, _make_sheet_rows, _parse_timestamp, read_workbook as _codec_read_workbook,
    template_bytes as _codec_template_bytes,
)

MAX_PENDING_CONFIRMATIONS = store.MAX_PENDING_CONFIRMATIONS
MAX_PENDING_CONFIRMATION_BYTES = store.MAX_PENDING_CONFIRMATION_BYTES

def read_workbook(content):
    """Compatibility wrapper preserving the module-level upload-limit setting."""
    return _codec_read_workbook(content, max_upload_bytes=MAX_UPLOAD_BYTES)

def _build_xlsx(snapshot, sample=False):
    """Compatibility wrapper for the historical private encoder entry point."""
    return _codec_build_xlsx(snapshot, sample=sample, max_workbook_bytes=MAX_WORKBOOK_BYTES)

def template_bytes():
    return _codec_template_bytes(max_workbook_bytes=MAX_WORKBOOK_BYTES)

def _tenant_workbook_state(data=None):
    """Preserve workbook schema compatibility without exporting global bot state."""
    result = store._new_tenant_data(data)
    result["bot"] = {"offset": 0, "session": None}
    return result

def snapshot_bytes(c=None, principal=None):
    if c is None:
        with store.engine.begin() as conn:
            store.lock_application(conn)
            return snapshot_bytes(conn, principal=principal)
    if principal is None:
        states = c.execute(select(store.state)).mappings().all()
        states = [dict(row, data={**row["data"], "bot": store.read_bot_runtime(c)}) for row in states]
        history = c.execute(select(store.portfolio_history)).mappings().all()
        catalog = c.execute(select(store.instruments)).mappings().all()
        cache = c.execute(select(store.stock_cache)).mappings().all()
    else:
        row = c.execute(select(store.tenant_ledger).where(store.tenant_ledger.c.principal == principal)).mappings().first()
        if row is None:
            state_data = _tenant_workbook_state()
            states = [{"id": 1, "schema_version": 1, "data": state_data}]
            history = []
        else:
            states = [{"id": 1, "schema_version": row["schema_version"],
                "data": _tenant_workbook_state(row["data"])}]
            history = c.execute(select(store.tenant_portfolio_history).where(
                store.tenant_portfolio_history.c.principal == principal)).mappings().all()
        # Market catalog and quote cache are shared infrastructure, not part of
        # a user's restorable financial archive.
        catalog, cache = [], []
    snapshot = {"planner_state": [_db_row(row) for row in states],
        "instrument_catalog": [_db_row(row) for row in catalog],
        "portfolio_history": [_db_row(row) for row in history],
        "stock_cache": [_db_row(row) for row in cache]}
    return _build_xlsx(snapshot)

def _current_snapshot(c, principal=None):
    if principal is None:
        states = c.execute(select(store.state)).mappings().all()
        states = [dict(row, data={**row["data"], "bot": store.read_bot_runtime(c)}) for row in states]
        return {"planner_state": [_db_row(row) for row in states],
                "instrument_catalog": [_db_row(row) for row in c.execute(select(store.instruments)).mappings().all()],
                "portfolio_history": [_db_row(row) for row in c.execute(select(store.portfolio_history)).mappings().all()],
                "stock_cache": [_db_row(row) for row in c.execute(select(store.stock_cache)).mappings().all()]}
    row = c.execute(select(store.tenant_ledger).where(store.tenant_ledger.c.principal == principal)).mappings().first()
    state = {"id": 1, "schema_version": 1, "data": _tenant_workbook_state()} if row is None else {
        "id": 1, "schema_version": row["schema_version"],
        "data": _tenant_workbook_state(row["data"])}
    history = c.execute(select(store.tenant_portfolio_history).where(
        store.tenant_portfolio_history.c.principal == principal)).mappings().all()
    return {"planner_state": [_db_row(state)], "instrument_catalog": [],
            "portfolio_history": [_db_row(item) for item in history], "stock_cache": []}

def _current_etag(snapshot):
    rows = _make_sheet_rows(snapshot)
    return sha256("".join(sheet + _digest(sheet_rows) for sheet, sheet_rows in rows.items()).encode()).hexdigest()

def validate_workbook(content, principal=None):
    workbook = read_workbook(content)
    tables = workbook["tables"]
    with store.engine.begin() as c:
        store.lock_application(c)
        current_snapshot = _current_snapshot(c, principal)
        current = current_snapshot["planner_state"][0]
        current_revision = current["data"]["revision"]
        current_counts = {"planner_state": 1, "instrument_catalog": len(current_snapshot["instrument_catalog"]),
            "portfolio_history": len(current_snapshot["portfolio_history"]), "stock_cache": len(current_snapshot["stock_cache"])}
        etag = _current_etag(current_snapshot)
    uploaded_counts = {"planner_state": 1, "instrument_catalog": len(tables["instrument_catalog"]),
        "portfolio_history": len(tables["portfolio_history"]), "stock_cache": len(tables["stock_cache"])}
    if principal is not None:
        uploaded_counts["instrument_catalog"] = 0
        uploaded_counts["stock_cache"] = 0
    workbook_hash = sha256(content).hexdigest()
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    created_at = time.time_ns()
    expires_at = now + 600
    store.create_workbook_confirmation(
        sha256(token.encode("utf-8")).hexdigest(), principal, workbook_hash,
        current_revision, etag, created_at, expires_at, content, now,
    )
    warnings = []
    if workbook["sample_data"]:
        warning_scope = "this tenant's " if principal is not None else ""
        warnings.append(f"This is synthetic sample data. Importing it replaces {warning_scope}current records with one example bank account and entry.")
    elif principal is not None:
        warnings.append("Import replaces only this user's ledger and portfolio history and creates a rollback snapshot.")
    else:
        warnings.append("Import replaces all data in four tables and creates a rollback snapshot.")
    if principal is not None:
        warnings.append("Tenant import changes only this user's ledger and history; shared market catalog and quote cache are ignored.")
    else:
        warnings.append("Telegram's update offset stays monotonic; pending conversation and reply state are cleared.")
    if tables["planner_state"]["data"]["revision"] != current_revision:
        warnings.append("Imported ledger revision differs from current revision.")
    return {"archive_sha256": workbook_hash, "current_revision": current_revision, "current_etag": etag,
        "format_version": workbook["format_version"], "schema_version": workbook["schema_version"],
        "app_version": workbook["app_version"], "exported_at": workbook["exported_at"],
        "counts": {"current": current_counts, "uploaded": uploaded_counts},
        "comparison": {"current": current_counts, "uploaded": uploaded_counts}, "warnings": warnings,
        "confirmation_token": token, "confirmation_expires_in_seconds": 600,
        "sample_data": workbook["sample_data"], "importable": True}

def _parse_import_tables(tables):
    state_row = dict(tables["planner_state"])
    instruments, history, cache = [], [], []
    for source in tables["instrument_catalog"]:
        item = dict(source)
        item["updated_at"] = _parse_timestamp(item["updated_at"], "Instrument Catalog updated_at")
        instruments.append(item)
    for source in tables["portfolio_history"]:
        item = dict(source)
        item["updated_at"] = _parse_timestamp(item["updated_at"], "Portfolio History updated_at")
        history.append(item)
    for source in tables["stock_cache"]:
        item = dict(source)
        item["fetched_at"] = _parse_timestamp(item["fetched_at"], "Stock Cache fetched_at")
        item["expires_at"] = _parse_timestamp(item["expires_at"], "Stock Cache expires_at")
        cache.append(item)
    return state_row, instruments, history, cache

def _write_rollback(snapshot):
    root = Path(os.environ.get("DATA_ROLLBACK_DIR", "/data/data-import-rollback"))
    root.mkdir(parents=True, exist_ok=True)
    content, info = _build_xlsx(snapshot)
    snapshot_id = sha256(content).hexdigest()[:16]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = root / f"rollback-{stamp}-{snapshot_id}.xlsx"
    fd, temp_name = tempfile.mkstemp(prefix="rollback-", suffix=".tmp", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {"id": snapshot_id, "created_at": info["exported_at"], "path": str(path)}

def _counts(tables):
    return {"planner_state": 1, "instrument_catalog": len(tables["instrument_catalog"]),
            "portfolio_history": len(tables["portfolio_history"]), "stock_cache": len(tables["stock_cache"])}

def commit_import(workbook_hash, current_revision, current_etag, token, principal=None):
    if not isinstance(token, str):
        raise WorkbookError("Confirmation token is invalid, expired, or already used")
    # The database matches tenant scope and atomically deletes the record before
    # checking payload hashes, preserving single-use behavior across processes.
    pending = store.consume_workbook_confirmation(
        sha256(token.encode("utf-8")).hexdigest(), principal, int(time.time()))
    if not pending:
        raise WorkbookError("Confirmation token is invalid, expired, or already used")
    if (not isinstance(workbook_hash, str) or not secrets.compare_digest(pending["workbook_sha256"], workbook_hash)
            or pending["current_revision"] != current_revision or not isinstance(current_etag, str)
            or not secrets.compare_digest(pending["current_etag"], current_etag)):
        raise WorkbookError("Confirmation token does not match the workbook or current revision")
    workbook = read_workbook(pending["workbook_bytes"])
    state_row, instruments, history, cache = _parse_import_tables(workbook["tables"])
    with store.engine.begin() as c:
        store.lock_application(c)
        live_snapshot = _current_snapshot(c, principal)
        live = live_snapshot["planner_state"][0]
        if live["data"].get("revision", 0) != current_revision:
            raise WorkbookError("Live data changed after validation; validate the workbook again")
        if not secrets.compare_digest(_current_etag(live_snapshot), current_etag):
            raise WorkbookError("Live data changed after validation; validate the workbook again")
        rollback = _write_rollback(live_snapshot)
        if principal is None:
            imported_bot = state_row["data"].setdefault("bot", {})
            current_bot = store.read_bot_runtime(c)
            imported_bot["offset"] = max(imported_bot.get("offset", 0), current_bot.get("offset", 0))
            imported_bot["session"] = None
            if "pending_reply" in imported_bot or "pending_reply" in current_bot:
                imported_bot["pending_reply"] = None
            if "pending_markup" in imported_bot or "pending_markup" in current_bot:
                imported_bot["pending_markup"] = None
            state_row["data"].pop("bot", None)
            c.execute(update(store.state).where(store.state.c.id == 1).values(
                schema_version=state_row["schema_version"], data=state_row["data"]))
            c.execute(update(store.bot_runtime).where(store.bot_runtime.c.id == 1).values(data=imported_bot))
            for table in (store.instruments, store.portfolio_history,
                          store.portfolio_deposit_history, store.stock_cache):
                c.execute(delete(table))
            if instruments:
                c.execute(insert(store.instruments), instruments)
            if history:
                c.execute(insert(store.portfolio_history), history)
            if cache:
                c.execute(insert(store.stock_cache), cache)
        else:
            tenant_data = store._new_tenant_data(state_row["data"])
            c.execute(delete(store.tenant_ledger).where(store.tenant_ledger.c.principal == principal))
            c.execute(store.tenant_ledger.insert().values(principal=principal,
                schema_version=state_row["schema_version"], data=tenant_data))
            c.execute(delete(store.tenant_portfolio_history).where(store.tenant_portfolio_history.c.principal == principal))
            c.execute(delete(store.tenant_portfolio_deposit_history).where(
                store.tenant_portfolio_deposit_history.c.principal == principal))
            if history:
                c.execute(insert(store.tenant_portfolio_history), [dict(row, principal=principal) for row in history])
    try:
        root = Path(os.environ.get("DATA_ROLLBACK_DIR", "/data/data-import-rollback"))
        snapshots = sorted(root.glob("rollback-*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True)
        for expired in snapshots[10:]:
            expired.unlink()
    except OSError:
        logging.exception("Import committed, but rollback snapshot retention cleanup failed")
    rollback.pop("path", None)
    counts = _counts(workbook["tables"])
    if principal is not None:
        counts["instrument_catalog"] = 0
        counts["stock_cache"] = 0
    notices = ([] if principal is not None else
        ["Telegram update offset kept monotonic; pending conversation and reply cleared."])
    return {"status": "committed", "counts": counts, "rollback_snapshot": rollback,
            "notices": notices}
