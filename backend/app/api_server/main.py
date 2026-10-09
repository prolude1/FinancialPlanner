import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select
from ..core import store
from ..core.domain import apply, Invalid
from ..core.finance import time_value
from ..core.schemas import (Login, TimeValue, TelegramLinkConfirm, RecurringScheduleCreate,
                            RecurringSchedulePatch, RecurringBotScheduleCreate, validate_command)
from ..core.command_service import (
    CommandConflictError,
    CommandFieldsError,
    CommandPayloadError,
    FinancialCommandService,
)
from ..core.stocks import StockProviderError, service as stock_service
from ..core.ledger_service import FinancialLedgerService
from ..core.views import dashboard, cashflow_summary
from ..core import data_workbook
from ..core import keycloak_identity, telegram_linking, recurring as recurring_service

ledger_service = FinancialLedgerService(store)

SECRET = os.environ.get("SESSION_SECRET", "")
if len(SECRET) < 32:
    raise RuntimeError("SESSION_SECRET must contain at least 32 characters; run scripts/configure.py")
PASSWORD_HASH = os.environ.get("WEB_PASSWORD_HASH", "")
BOT_SECRET = os.environ.get("BOT_API_SECRET", "")
ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:8080").rstrip("/")
app = FastAPI(docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="strict", max_age=43200, session_cookie="planner_session")
attempts = {}
claim_attempts = {}


def today():
    return datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()


command_service = FinancialCommandService(
    ledger_service, apply, validate_command, today
)


def apply_financial_command(command, payload, *, principal, actor, idempotency_key,
                            telegram_user_id=None):
    try:
        return command_service.execute(
            command,
            payload,
            principal=principal,
            actor=actor,
            idempotency_key=idempotency_key,
            telegram_user_id=telegram_user_id,
        )
    except CommandPayloadError as exc:
        raise HTTPException(422, f"Invalid {exc.field}: {exc}") from exc
    except CommandConflictError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except CommandFieldsError as exc:
        raise HTTPException(422, str(exc)) from exc


def verify_password(password):
    try:
        salt, expected = PASSWORD_HASH.split(":")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def identity(request):
    token = request.headers.get("authorization", "")
    if BOT_SECRET and hmac.compare_digest(token, "Bearer " + BOT_SECRET):
        return "bot"
    if token.lower().startswith("bearer "):
        keycloak_principal(request)
        return "web"
    if request.session.get("owner"):
        return "web"
    raise HTTPException(401, "Sign in to continue")


def csrf(request):
    if request.headers.get("origin") != ORIGIN:
        raise HTTPException(403, "This request must come from the local dashboard")
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), request.session.get("csrf", "-")):
        raise HTTPException(403, "Refresh the page and try again")


def browser_owner(request):
    if not request.session.get("owner"):
        raise HTTPException(401, "Sign in to continue")
    return "web"


def browser_mutation(request):
    browser_owner(request)
    csrf(request)


def verified_keycloak_principal(request):
    """Validate the bearer and derive the stable principal without storage I/O."""
    try:
        return keycloak_identity.principal_from_authorization(
            request.headers.get("authorization", "")
        )
    except keycloak_identity.IdentityConfigurationError as exc:
        raise HTTPException(503, detail={"code": "identity_not_configured", "message": str(exc)}) from exc
    except keycloak_identity.IdentityProviderUnavailable as exc:
        raise HTTPException(503, detail={"code": "identity_provider_unavailable", "message": str(exc)}) from exc
    except keycloak_identity.InvalidIdentityToken:
        raise HTTPException(401, detail={"code": "invalid_access_token", "message": "A valid Keycloak access token is required"}) from None


def keycloak_principal(request):
    """Validate Keycloak identity and preserve first-request tenant provisioning."""
    principal = verified_keycloak_principal(request)
    # Non-ledger endpoints historically provision on a user's first verified
    # request. Ledger routes provision/read through their single service call.
    ledger_service.read(principal)
    return principal


def ledger_access(request):
    """Return (principal, actor); None principal denotes the unclaimed legacy store."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer ") and not (BOT_SECRET and hmac.compare_digest(
            authorization, "Bearer " + BOT_SECRET)):
        return verified_keycloak_principal(request), "web"
    actor = identity(request)
    try:
        store.assert_legacy_unclaimed()
    except PermissionError:
        raise HTTPException(403, detail={"code": "legacy_ledger_claimed",
            "message": "This legacy session cannot access the claimed ledger; sign in with Keycloak"}) from None
    return None, actor


def telegram_link_bot_secret():
    secret = os.environ.get("TELEGRAM_LINK_BOT_SECRET", "")
    legacy_secret = os.environ.get("BOT_API_SECRET", "")
    if len(secret) < 32 or not secret.isascii() or (legacy_secret and hmac.compare_digest(secret.encode(), legacy_secret.encode())):
        return None
    return secret


def internal_link_bot(request):
    secret = telegram_link_bot_secret()
    if secret is None:
        raise HTTPException(503, detail={"code": "link_bot_not_configured", "message": "Telegram link confirmation is not configured"})
    if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
        raise HTTPException(401, detail={"code": "internal_auth_required", "message": "Internal bot authentication required"})


def linked_bot_principal(request, body):
    secret = BOT_SECRET
    if len(secret) < 32 or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
        raise HTTPException(401, detail={"code": "internal_auth_required", "message": "Internal bot authentication required"})
    if (not isinstance(body, dict) or not all(isinstance(body.get(key), int)
            and not isinstance(body.get(key), bool) and 0 < body[key] <= 4503599627370495
            for key in ("telegram_user_id", "telegram_chat_id"))
            or body["telegram_user_id"] != body["telegram_chat_id"]):
        raise HTTPException(403, detail={"code": "telegram_not_linked", "message": "Link this private Telegram account to continue"})
    with store.engine.connect() as connection:
        principal = connection.execute(select(store.telegram_connections.c.principal).where(
            store.telegram_connections.c.telegram_user_id == body["telegram_user_id"])).scalar_one_or_none()
    if principal is None:
        raise HTTPException(403, detail={"code": "telegram_not_linked", "message": "Link this private Telegram account to continue"})
    return principal, body["telegram_user_id"]


def archive_error(exc):
    message = str(exc)
    status = 409 if "changed after validation" in message or "confirmation token" in message.lower() else 422
    return HTTPException(status, detail={"code": "workbook_conflict" if status == 409 else "invalid_workbook",
                                         "message": message})


@app.exception_handler(Invalid)
async def invalid_handler(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(StockProviderError)
async def stock_error_handler(request, exc):
    status = 422 if not exc.retryable else 503
    return JSONResponse(status_code=status, content={"detail": {"code": exc.code,
        "message": exc.message, "retryable": exc.retryable, "stale_available": False}})


@app.get("/health")
def health():
    store.read()
    return {"status": "ok"}


@app.post("/api/login")
async def login(request: Request):
    if request.headers.get("origin") != ORIGIN:
        raise HTTPException(403, "Use the configured local dashboard URL")
    now = time.monotonic()
    recent = [t for t in attempts.get("owner", []) if t > now - 300]
    attempts["owner"] = recent
    if len(recent) >= 10:
        raise HTTPException(429, "Too many attempts; try again in five minutes")
    try:
        body = Login.model_validate(await request.json())
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Expected a JSON object with a string password") from None
    if not verify_password(body.password):
        recent.append(now)
        raise HTTPException(401, "Incorrect password")
    attempts.clear()
    request.session.clear()
    request.session.update(owner=True, csrf=secrets.token_urlsafe(32))
    return {"csrf": request.session["csrf"]}


@app.get("/api/session")
def session(request: Request):
    identity(request)
    return {"csrf": request.session.get("csrf")}


@app.post("/api/me/telegram-link/challenge", status_code=201)
def create_telegram_link_challenge(request: Request, response: Response):
    principal = keycloak_principal(request)
    if telegram_link_bot_secret() is None:
        raise HTTPException(503, detail={"code": "link_bot_not_configured", "message": "Telegram link confirmation is not configured"})
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot_username):
        raise HTTPException(503, detail={"code": "link_bot_not_configured", "message": "Telegram bot username is not configured"})
    response.headers["Cache-Control"] = "no-store"
    try:
        result = telegram_linking.create_challenge(principal)
        result["bot_url"] = f"https://t.me/{bot_username}?start=link_{result['challenge']}"
        return result
    except telegram_linking.TelegramLinkError as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/me/telegram-link")
def get_telegram_link_status(request: Request, response: Response):
    principal = keycloak_principal(request)
    response.headers["Cache-Control"] = "no-store"
    return telegram_linking.status(principal)


@app.delete("/api/me/telegram-link")
def unlink_telegram_account(request: Request, response: Response):
    principal = keycloak_principal(request)
    response.headers["Cache-Control"] = "no-store"
    return telegram_linking.unlink(principal)


@app.get("/api/me")
def get_current_user(request: Request, response: Response):
    principal = keycloak_principal(request)
    now = int(time.time())
    with store.engine.connect() as connection:
        claim = connection.execute(select(store.legacy_claim.c.claimed_by).where(
            store.legacy_claim.c.id == 1)).scalar_one_or_none()
        available = (claim is None and connection.execute(select(store.legacy_claim_codes.c.token_hash).where(
            store.legacy_claim_codes.c.consumed_at.is_(None),
            store.legacy_claim_codes.c.expires_at > now)).first() is not None)
    response.headers["Cache-Control"] = "no-store"
    return {"legacy_claim": {"available": bool(available), "completed": claim == principal}}


@app.post("/api/owner-claim", status_code=200)
async def claim_legacy_ledger(request: Request, response: Response):
    principal = keycloak_principal(request)
    response.headers["Cache-Control"] = "no-store"
    source = request.client.host if request.client else "unknown"
    attempt_key = (principal, source)
    now = time.monotonic()
    recent = [timestamp for timestamp in claim_attempts.get(attempt_key, []) if timestamp > now - 600]
    claim_attempts[attempt_key] = recent
    if len(recent) >= 10:
        raise HTTPException(429, detail={"code": "claim_failed", "message": "Claim code is invalid, expired, or already used"})
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    token = body.get("code") if isinstance(body, dict) and set(body) == {"code"} else None
    if not isinstance(token, str) or not 30 <= len(token) <= 128:
        recent.append(now)
        raise HTTPException(400, detail={"code": "claim_failed", "message": "Claim code is invalid, expired, or already used"})
    try:
        result = store.redeem_legacy_claim(principal, token)
        claim_attempts.pop(attempt_key, None)
        return result
    except ValueError:
        recent.append(now)
        raise HTTPException(400, detail={"code": "claim_failed", "message": "Claim code is invalid, expired, or already used"}) from None


@app.post("/internal/bot/telegram-link/confirm")
def confirm_telegram_link(payload: TelegramLinkConfirm, request: Request, response: Response):
    internal_link_bot(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return telegram_linking.confirm_challenge(payload.challenge, payload.telegram_user_id,
                                                  payload.telegram_chat_id)
    except telegram_linking.TelegramLinkError as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc


@app.post("/internal/bot/financial/dashboard")
async def bot_financial_dashboard(request: Request, response: Response):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict) or set(body) != {"telegram_user_id", "telegram_chat_id"}:
        raise HTTPException(422, detail={"code": "invalid_bot_request", "message": "Invalid private Telegram request"})
    principal, telegram_user_id = linked_bot_principal(request, body)
    try:
        result = dashboard(
            ledger_service.read(principal, telegram_user_id=telegram_user_id),
            today(), actor="bot",
        )
    except PermissionError:
        raise HTTPException(403, detail={"code": "telegram_not_linked", "message": "Link this private Telegram account to continue"}) from None
    result["portfolio_history"] = ledger_service.portfolio_history(principal)
    response.headers["Cache-Control"] = "no-store"
    return result


@app.post("/internal/bot/financial/command/{command}")
async def bot_financial_command(command: str, request: Request):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if (not isinstance(body, dict) or set(body) != {"telegram_user_id", "telegram_chat_id", "payload"}
            or not isinstance(body.get("payload"), dict)):
        raise HTTPException(422, detail={"code": "invalid_bot_request", "message": "Invalid private Telegram request"})
    principal, _ = linked_bot_principal(request, body)
    try:
        return apply_financial_command(
            command,
            body["payload"],
            principal=principal,
            actor="bot",
            idempotency_key=request.headers.get("idempotency-key", ""),
            telegram_user_id=body["telegram_user_id"],
        )
    except PermissionError:
        raise HTTPException(403, detail={"code": "telegram_not_linked", "message": "Link this private Telegram account to continue"}) from None


@app.post("/internal/bot/financial/state")
async def bot_financial_state(request: Request, response: Response):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict) or body.get("action") not in ("get", "set", "clear"):
        raise HTTPException(422, detail={"code": "invalid_bot_request", "message": "Invalid private Telegram request"})
    required = {"telegram_user_id", "telegram_chat_id", "action"}
    state_fields = {"session", "pending_reply", "pending_markup", "last_tvm"}
    if (body["action"] != "set" and set(body) != required
            or body["action"] == "set" and (not state_fields.intersection(body) or set(body) - required - state_fields)):
        raise HTTPException(422, detail={"code": "invalid_bot_request", "message": "Invalid private Telegram request"})
    principal, telegram_user_id = linked_bot_principal(request, body)
    response.headers["Cache-Control"] = "no-store"
    if body["action"] == "clear":
        try:
            store.write_telegram_user_state(principal, telegram_user_id,
                {"session": None, "pending_reply": None, "pending_markup": None, "last_tvm": None})
        except PermissionError:
            raise HTTPException(403, detail={"code": "telegram_not_linked", "message": "Link this private Telegram account to continue"}) from None
        return {"session": None, "pending_reply": None, "pending_markup": None, "last_tvm": None}
    if body["action"] == "get":
        try:
            return store.read_telegram_user_state(principal, telegram_user_id)
        except PermissionError:
            raise HTTPException(403, detail={"code": "telegram_not_linked", "message": "Link this private Telegram account to continue"}) from None
    patch = {key: body[key] for key in state_fields if key in body}
    if ("session" in patch and patch["session"] is not None and not isinstance(patch["session"], dict)
            or "pending_reply" in patch and patch["pending_reply"] is not None and not isinstance(patch["pending_reply"], str)
            or "pending_markup" in patch and patch["pending_markup"] is not None and not isinstance(patch["pending_markup"], dict)
            or "last_tvm" in patch and patch["last_tvm"] is not None and not isinstance(patch["last_tvm"], dict)):
        raise HTTPException(422, detail={"code": "invalid_bot_request", "message": "Invalid private Telegram state"})
    try:
        value = store.patch_telegram_user_state(principal, telegram_user_id, patch)
    except PermissionError:
        raise HTTPException(403, detail={"code": "telegram_not_linked", "message": "Link this private Telegram account to continue"}) from None
    return value


@app.post("/internal/bot/financial/recurring-transactions", status_code=201)
async def bot_create_recurring_transaction(request: Request, response: Response):
    try:
        raw = await request.json()
        payload = RecurringBotScheduleCreate.model_validate(raw)
    except Exception:
        raise HTTPException(422, detail={"code": "invalid_recurring_schedule",
            "message": "Provide a valid private Telegram recurring schedule request"}) from None
    principal, telegram_user_id = linked_bot_principal(request, raw)
    idempotency_key = request.headers.get("idempotency-key", "")
    if not idempotency_key or len(idempotency_key) > 160:
        raise HTTPException(422, detail={"code": "invalid_idempotency_key",
            "message": "A valid Idempotency-Key header is required"})
    try:
        with ledger_service.transaction(principal, telegram_user_id=telegram_user_id) as state:
            result = recurring_service.create_schedule(
                state, payload.schedule.model_dump(), today(), actor="bot",
                idempotency_key=idempotency_key)
    except Invalid as exc:
        raise HTTPException(422, detail={"code": "invalid_recurring_schedule", "message": str(exc)}) from exc
    except PermissionError:
        raise HTTPException(403, detail={"code": "telegram_not_linked",
            "message": "Link this private Telegram account to continue"}) from None
    response.headers["Cache-Control"] = "no-store"
    return {"schedule": result}


@app.post("/api/logout")
def logout(request: Request):
    csrf(request)
    request.session.clear()
    return {"ok": True}


@app.get("/api/revision")
def revision(request: Request):
    principal, _ = ledger_access(request)
    s = ledger_service.read(principal)
    return {"revision": s["revision"], "date": str(today())}


@app.get("/api/data-export")
def export_data(request: Request):
    principal = keycloak_principal(request)
    try:
        content, manifest = data_workbook.snapshot_bytes(principal=principal)
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc
    stamp = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).strftime("%Y%m%d-%H%M%S")
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="financial-planner-export-{stamp}.xlsx"',
                             "Cache-Control": "no-store", "X-Workbook-Format-Version": str(manifest["format_version"])})


@app.get("/api/data-template")
def data_template(request: Request):
    keycloak_principal(request)
    try:
        content, _ = data_workbook.template_bytes()
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="financial-planner-data-template.xlsx"',
                             "Cache-Control": "no-store"})


@app.post("/api/data-import/validate")
async def validate_data_import(request: Request):
    principal = keycloak_principal(request)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > data_workbook.MAX_UPLOAD_BYTES + 1024 * 1024:
        raise HTTPException(413, detail={"code": "upload_too_large", "message": "Workbook exceeds the 64 MiB upload limit"})
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, detail={"code": "missing_file", "message": "Upload an Excel .xlsx workbook in the file field"})
    if not upload.filename or not upload.filename.lower().endswith(".xlsx"):
        raise HTTPException(422, detail={"code": "invalid_file_type", "message": "Choose an Excel .xlsx workbook"})
    content = await upload.read(data_workbook.MAX_UPLOAD_BYTES + 1)
    if len(content) > data_workbook.MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail={"code": "upload_too_large", "message": "Workbook exceeds the 64 MiB upload limit"})
    try:
        return data_workbook.validate_workbook(content, principal=principal)
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc


@app.post("/api/data-import/commit")
async def commit_data_import(request: Request):
    principal = keycloak_principal(request)
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, detail={"code": "invalid_confirmation", "message": "Expected JSON confirmation fields"}) from None
    if not isinstance(body, dict) or set(body) != {"archive_sha256", "current_revision", "current_etag", "confirmation_token"}:
        raise HTTPException(422, detail={"code": "invalid_confirmation", "message": "Provide archive_sha256, current_revision, current_etag, and confirmation_token"})
    try:
        return data_workbook.commit_import(body["archive_sha256"], body["current_revision"],
                                          body["current_etag"], body["confirmation_token"], principal=principal)
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc


@app.get("/api/dashboard")
def get_dashboard(request: Request):
    principal, actor = ledger_access(request)
    snapshot = ledger_service.read(principal)
    result = dashboard(snapshot, today(), actor=actor)
    result["portfolio_history"] = ledger_service.portfolio_history(principal)
    return result


@app.get("/api/cashflow")
def get_cashflow(request: Request, year: int | None = Query(default=None, ge=1970, le=9999)):
    principal, _ = ledger_access(request)
    current = today()
    if year is not None and year > current.year:
        raise HTTPException(422, "Future calendar years are not available")
    snapshot = ledger_service.read(principal)
    return cashflow_summary(snapshot, current, year=year)


@app.get("/api/recurring-transactions")
def list_recurring_transactions(request: Request, response: Response):
    principal = keycloak_principal(request)
    snapshot = ledger_service.read(principal)
    response.headers["Cache-Control"] = "no-store"
    return {"as_of": str(today()), "schedules": recurring_service.list_schedules(snapshot)}


@app.post("/api/recurring-transactions", status_code=201)
def create_recurring_transaction(payload: RecurringScheduleCreate, request: Request, response: Response):
    principal = keycloak_principal(request)
    try:
        with ledger_service.transaction(principal) as state:
            idempotency_key = request.headers.get("idempotency-key")
            schedule = recurring_service.create_schedule(state, payload.model_dump(), today(),
                actor="web", idempotency_key=idempotency_key)
    except Invalid as exc:
        raise HTTPException(422, detail={"code": "invalid_recurring_schedule", "message": str(exc)}) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"schedule": schedule}


@app.patch("/api/recurring-transactions/{schedule_id}")
def update_recurring_transaction(schedule_id: str, payload: RecurringSchedulePatch,
                                 request: Request, response: Response):
    principal = keycloak_principal(request)
    try:
        with ledger_service.transaction(principal) as state:
            schedule = recurring_service.update_schedule(
                state, schedule_id, payload.model_dump(exclude_unset=True), today())
    except KeyError:
        raise HTTPException(404, detail={"code": "recurring_schedule_not_found",
            "message": "Recurring schedule not found"}) from None
    except RuntimeError as exc:
        raise HTTPException(409, detail={"code": "recurring_schedule_conflict", "message": str(exc)}) from exc
    except Invalid as exc:
        raise HTTPException(422, detail={"code": "invalid_recurring_schedule", "message": str(exc)}) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"schedule": schedule}


@app.delete("/api/recurring-transactions/{schedule_id}", status_code=200)
def delete_recurring_transaction(schedule_id: str, request: Request, response: Response):
    principal = keycloak_principal(request)
    try:
        with ledger_service.transaction(principal) as state:
            schedule = recurring_service.delete_schedule(state, schedule_id)
    except KeyError:
        raise HTTPException(404, detail={"code": "recurring_schedule_not_found",
            "message": "Recurring schedule not found"}) from None
    response.headers["Cache-Control"] = "no-store"
    return {"schedule": schedule}


@app.post("/api/calculators/time-value")
def calculate_time_value(payload: TimeValue, request: Request):
    actor = identity(request)
    if actor == "web" and not request.headers.get("authorization", "").lower().startswith("bearer "):
        csrf(request)
    return time_value(payload.model_dump(exclude_none=True))


@app.get("/api/stocks/search")
def search_stocks(request: Request, q: str = Query(min_length=1, max_length=40),
                  exchange: str | None = Query(default=None)):
    identity(request)
    exchange = exchange.upper() if exchange else None
    if exchange and exchange not in ("NASDAQ", "NYSE", "LSE", "SGX"):
        raise StockProviderError("invalid_exchange", "Choose NASDAQ, NYSE, LSE or SGX", False)
    return stock_service.search(q.strip(), exchange)


@app.get("/api/stocks/{security_id}/overview")
def stock_overview(security_id: str, request: Request):
    identity(request)
    return stock_service.overview(security_id)


@app.get("/api/stocks/{security_id}/identity")
def stock_identity(security_id: str, request: Request):
    identity(request)
    return stock_service.identity(security_id)


@app.get("/api/stocks/{security_id}/candles")
def stock_candles(security_id: str, request: Request,
                  range_name: str = Query(default="5y", alias="range"),
                  interval: str = Query(default="1d")):
    identity(request)
    if range_name not in ("1y", "3y", "5y", "max") or interval != "1d":
        raise StockProviderError("invalid_candle_query", "Use range 1y, 3y, 5y or max and interval 1d", False)
    return stock_service.candles(security_id, range_name)


@app.get("/api/stocks/{security_id}/financials")
def stock_financials(security_id: str, request: Request,
                     years: int = Query(default=5, ge=1, le=5)):
    identity(request)
    return stock_service.financials(security_id, years)


@app.get("/api/stocks/{security_id}/earnings/latest")
def stock_latest_earnings(security_id: str, request: Request):
    identity(request)
    return stock_service.latest_earnings(security_id)


@app.post("/api/commands/{command}")
async def mutate(command: str, request: Request):
    principal, actor = ledger_access(request)
    if actor == "web" and principal is None:
        csrf(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "Expected an object")
    return apply_financial_command(
        command,
        body,
        principal=principal,
        actor=actor,
        idempotency_key=request.headers.get("idempotency-key", ""),
    )
