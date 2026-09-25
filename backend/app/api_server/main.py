import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from pydantic import ValidationError
from ..core import store
from ..core.domain import apply, Invalid, TransactionConflict
from ..core.finance import time_value
from ..core.schemas import Login, TimeValue, validate_command
from ..core.stocks import StockProviderError, service as stock_service
from ..core.views import dashboard, cashflow_summary
from ..core import data_workbook

SECRET = os.environ.get("SESSION_SECRET", "")
if len(SECRET) < 32:
    raise RuntimeError("SESSION_SECRET must contain at least 32 characters; run scripts/configure.py")
PASSWORD_HASH = os.environ.get("WEB_PASSWORD_HASH", "")
BOT_SECRET = os.environ.get("BOT_API_SECRET", "")
ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:8080").rstrip("/")
app = FastAPI(docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="strict", max_age=43200, session_cookie="planner_session")
attempts = {}


def today():
    return datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).date()


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


@app.post("/api/logout")
def logout(request: Request):
    csrf(request)
    request.session.clear()
    return {"ok": True}


@app.get("/api/revision")
def revision(request: Request):
    identity(request)
    s = store.read()
    return {"revision": s["revision"], "date": str(today())}


@app.get("/api/data-export")
def export_data(request: Request):
    browser_owner(request)
    try:
        content, manifest = data_workbook.snapshot_bytes()
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc
    stamp = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Singapore"))).strftime("%Y%m%d-%H%M%S")
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="financial-planner-export-{stamp}.xlsx"',
                             "Cache-Control": "no-store", "X-Workbook-Format-Version": str(manifest["format_version"])})


@app.get("/api/data-template")
def data_template(request: Request):
    browser_owner(request)
    try:
        content, _ = data_workbook.template_bytes()
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="financial-planner-data-template.xlsx"',
                             "Cache-Control": "no-store"})


@app.post("/api/data-import/validate")
async def validate_data_import(request: Request):
    browser_mutation(request)
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
        return data_workbook.validate_workbook(content)
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc


@app.post("/api/data-import/commit")
async def commit_data_import(request: Request):
    browser_mutation(request)
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, detail={"code": "invalid_confirmation", "message": "Expected JSON confirmation fields"}) from None
    if not isinstance(body, dict) or set(body) != {"archive_sha256", "current_revision", "current_etag", "confirmation_token"}:
        raise HTTPException(422, detail={"code": "invalid_confirmation", "message": "Provide archive_sha256, current_revision, current_etag, and confirmation_token"})
    try:
        return data_workbook.commit_import(body["archive_sha256"], body["current_revision"],
                                          body["current_etag"], body["confirmation_token"])
    except data_workbook.WorkbookError as exc:
        raise archive_error(exc) from exc


@app.get("/api/dashboard")
def get_dashboard(request: Request):
    actor = identity(request)
    result = dashboard(store.read(), today(), actor=actor)
    result["portfolio_history"] = store.read_portfolio_history()
    return result


@app.get("/api/cashflow")
def get_cashflow(request: Request, year: int | None = Query(default=None, ge=1970, le=9999)):
    identity(request)
    current = today()
    if year is not None and year > current.year:
        raise HTTPException(422, "Future calendar years are not available")
    return cashflow_summary(store.read(), current, year=year)


@app.post("/api/calculators/time-value")
def calculate_time_value(payload: TimeValue, request: Request):
    actor = identity(request)
    if actor == "web":
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
    actor = identity(request)
    if actor == "web":
        csrf(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "Expected an object")
    try:
        body = validate_command(command, body)
    except ValidationError as exc:
        issue = exc.errors(include_url=False)[0]
        field = ".".join(str(part) for part in issue["loc"]) or "request"
        raise HTTPException(422, f"Invalid {field}: {issue['msg']}") from exc
    try:
        with store.transaction() as s:
            return apply(s, command, body, actor, request.headers.get("idempotency-key", ""), today())
    except TransactionConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except (KeyError, TypeError) as exc:
        raise HTTPException(422, "Missing or invalid operation fields") from exc
