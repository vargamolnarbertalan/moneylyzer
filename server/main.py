"""HTTP API + live stream + the built React app, on one port.

Run: uvicorn server.main:app --host 0.0.0.0 --port 8080   (the Docker image does this)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import pandas as pd
from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from analyzer import advisor, db, llm, prefs, research
from analyzer.persona import MAX_RULE_CHARS, MAX_RULES
from analyzer.engine import MINOR_UNITS, analyze_history, bootstrap_scenarios, latest_price_currency
from analyzer.portfolio import value_history
from analyzer.market import CURRENCY_RE, download_fund_nav, fund_isin, fx_symbol, live_quote, search_instruments
from analyzer.portfolio import is_yahoo_symbol
from analyzer.validation import ASSET_TYPE_CODES, LOTS_HEADER, validate_holdings, validate_lots
from server import __version__, auth, claude_login
from server.live import clean, hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lia")
ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(os.environ.get("LIA_STATIC_DIR") or ROOT / "frontend" / "dist")
COOKIE_SECURE = os.environ.get("LIA_COOKIE_SECURE") == "1"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# Request bodies: JSON is small (the largest is a 20 000-character persona); CSV uploads are capped at
# MAX_UPLOAD_BYTES plus multipart overhead. Checked before the body is parsed or spooled to /tmp.
MAX_JSON_BODY = 1024 * 1024
MAX_MULTIPART_BODY = MAX_UPLOAD_BYTES + 1024 * 1024
DEFAULT_DONATE_URL = "https://revolut.me/vargamolnarb"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    auth.init()
    research.init_tables()
    advisor.init_tables()
    prefs.init_tables()
    llm.sweep_system_files()  # prompt files left by a call that was killed mid-way
    task = asyncio.create_task(hub.run_forever())
    yield
    task.cancel()


app = FastAPI(title="Moneylyzer", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class BodyLimit:
    """Refuses oversized request bodies with 413 before anything reads them: by Content-Length when
    the client sends one, and by counting the bytes of a chunked body as it streams in."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        ctype = headers.get(b"content-type", b"").decode("latin-1").lower()
        limit = MAX_MULTIPART_BODY if ctype.startswith("multipart/") else MAX_JSON_BODY
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = True
            if too_big:
                return await self._refuse(send)
        seen = 0
        refused = False

        async def counted():
            # A chunked body is counted as it arrives. Past the limit the 413 goes out at once and the
            # app is told the client left (FastAPI would turn an exception here into its own 400).
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    refused = True
                    await self._refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not refused:  # after a refusal nothing the app answers may reach the client
                await send(message)

        await self.app(scope, counted, guarded_send)

    @staticmethod
    async def _refuse(send):
        body = b'{"detail":"Request body too large."}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})



app.add_middleware(BodyLimit)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def jsonable(obj) -> JSONResponse:
    return JSONResponse(clean(obj))


# ---------------------------------------------------------------- auth
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupBody(Credentials):
    code: str = Field(min_length=1, max_length=32)


class PasswordChange(BaseModel):
    old: str = Field(max_length=256)
    new: str = Field(max_length=256)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=COOKIE_SECURE, path="/")


@app.get("/api/health")
def health():
    return {"ok": True}


REPOSITORY_URL = "https://github.com/vargamolnarbertalan/moneylyzer"


def _donate_url() -> str | None:
    """Optional 'support the project' link; LIA_DONATE_URL="" hides it (e.g. in forks)."""
    url = os.environ.get("LIA_DONATE_URL", DEFAULT_DONATE_URL).strip()
    return url if url.startswith("https://") else None


@app.get("/api/meta")
def meta():
    return {"version": __version__, "donate_url": _donate_url(),
            "repository": REPOSITORY_URL}


@app.get("/api/auth/state")
def auth_state(request: Request):
    user = auth.session_user(request.cookies.get(auth.COOKIE))
    return {"setup_required": auth.user_count() == 0, "user": user}


@app.post("/api/auth/setup", dependencies=[Depends(auth.require_csrf)])
def auth_setup(body: SetupBody, request: Request, response: Response):
    auth.setup(body.username, body.password, body.code)
    _set_cookie(response, auth.login(body.username, body.password, auth.client_ip(request)))
    return {"user": body.username.strip()}


@app.post("/api/auth/login", dependencies=[Depends(auth.require_csrf)])
def auth_login(body: Credentials, request: Request, response: Response):
    _set_cookie(response, auth.login(body.username, body.password, auth.client_ip(request)))
    return {"user": body.username.strip()}


@app.post("/api/auth/logout", dependencies=[Depends(auth.require_csrf)])
def auth_logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.exception_handler(UnicodeEncodeError)
async def _invalid_text(request: Request, exc: UnicodeEncodeError):
    """JSON can carry lone UTF-16 surrogates; SQLite and pandas refuse them before anything is written."""
    return JSONResponse({"detail": "The text contains an invalid character."}, status_code=422)


# ---------------------------------------------------------------- "not financial advice" notice
# The wording and its version live in frontend/src/legal.ts; the server only records which version the
# owner accepted and when, so a reworded notice is shown (and accepted) again.
class DisclaimerBody(BaseModel):
    version: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(\.[0-9]{1,3})?$")

    @field_validator("version")
    @classmethod
    def _real_date(cls, v: str) -> str:
        pd.Timestamp(datetime.strptime(v[:10], "%Y-%m-%d"))  # ValueError -> 422 for 2026-99-99
        return v


def _disclaimer_history() -> list[dict]:
    raw = db.get_setting("disclaimer_accepted")
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        data = []
    if isinstance(data, dict):  # the first format stored only the latest acceptance
        data = [data]
    return [d for d in data if isinstance(d, dict)]


def _disclaimer_state() -> dict:
    last = (_disclaimer_history() or [{}])[-1]
    return {"accepted_version": last.get("version"), "accepted_at": last.get("at")}


@app.get("/api/disclaimer")
def disclaimer_get(user: str = Depends(auth.require_user)):
    return _disclaimer_state()


@app.post("/api/disclaimer/accept")
def disclaimer_accept(body: DisclaimerBody, user: str = Depends(auth.require_user)):
    at = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    history = _disclaimer_history() + [{"version": body.version, "at": at, "user": user}]
    db.set_setting("disclaimer_accepted", json.dumps(history[-20:]))  # every acceptance, newest last
    log.info("disclaimer %s accepted by %s", body.version, user)
    return _disclaimer_state()


@app.post("/api/auth/password")
def auth_password(body: PasswordChange, request: Request, user: str = Depends(auth.require_user)):
    auth.change_password(user, body.old, body.new, request.cookies.get(auth.COOKIE))
    return {"ok": True}


# ---------------------------------------------------------------- portfolio + live
@app.get("/api/portfolio")
async def portfolio(user: str = Depends(auth.require_user)):
    return jsonable(hub.snapshot or await hub.refresh())


@app.post("/api/portfolio/refresh")
async def portfolio_refresh(user: str = Depends(auth.require_user)):
    return jsonable(await hub.refresh(manual=True))


RANGE_DAYS = {"1W": 8, "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "3Y": 1096, "ALL": None}


@app.get("/api/portfolio/history")
async def portfolio_history(range: Literal["1W", "1M", "3M", "6M", "1Y", "3Y", "ALL"] = "1Y",
                            user: str = Depends(auth.require_user)):
    return jsonable({"range": range, **await asyncio.to_thread(value_history, RANGE_DAYS[range], db.base_currency())})


@app.get("/api/stream")
async def stream(request: Request, user: str = Depends(auth.require_user)):
    queue = hub.subscribe()

    async def events():
        try:
            if hub.snapshot:
                yield f"event: snapshot\ndata: {json.dumps(hub.snapshot)}\n\n"
            while not await request.is_disconnected():
                try:
                    snap = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- settings (reporting currency)
COMMON_CURRENCIES = ["HUF", "EUR", "USD", "GBP", "CHF", "PLN", "CZK", "RON", "SEK", "NOK", "DKK", "JPY", "CNY",
                     "AUD", "CAD"]


class AppSettingsBody(BaseModel):
    base_currency: str = Field(min_length=3, max_length=3)


@app.get("/api/settings")
def app_settings(user: str = Depends(auth.require_user)):
    return {"base_currency": db.base_currency(), "currencies": COMMON_CURRENCIES}


@app.put("/api/settings")
async def app_settings_save(body: AppSettingsBody, user: str = Depends(auth.require_user)):
    code = body.base_currency.strip().upper()
    if not CURRENCY_RE.fullmatch(code):
        raise HTTPException(400, "Use a 3-letter currency code such as EUR or HUF.")
    if code != "USD":
        quote = await asyncio.to_thread(live_quote, fx_symbol(code))
        if not quote.get("price") or quote.get("currency") != code:
            raise HTTPException(400, f"No exchange rate available for {code} (Yahoo symbol {fx_symbol(code)}).")
    db.set_setting("base_currency", code)
    await hub.refresh(force=True)
    return {"base_currency": code, "currencies": COMMON_CURRENCIES}


# ---------------------------------------------------------------- view preferences (synced across devices)
@app.get("/api/prefs")
def prefs_get(user: str = Depends(auth.require_user)):
    return prefs.get_all()


@app.patch("/api/prefs")
def prefs_patch(changes: dict = Body(...), user: str = Depends(auth.require_user)):
    """Set or clear (null) only the keys sent; returns every stored preference."""
    if len(changes) > 60:
        raise HTTPException(422, "Too many preferences in one request.")
    try:
        return prefs.update(changes)
    except prefs.PrefsError as exc:
        raise HTTPException(422, str(exc)) from None


# ---------------------------------------------------------------- instruments
class InstrumentBody(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    asset_type: str
    quantity: float = Field(ge=0)
    avg_cost: float | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)
    market_symbol: str | None = Field(default=None, max_length=64)
    price_divisor: float = Field(default=1.0, gt=0)
    notes: str | None = Field(default=None, max_length=1000)


@app.get("/api/instruments")
def instruments(user: str = Depends(auth.require_user)):
    return jsonable(db.get_instruments().to_dict("records"))


@app.post("/api/instruments")
async def instrument_save(body: InstrumentBody, create: bool = False, user: str = Depends(auth.require_user)):
    """Add or update a holding. With ?create=1 (the add-holding wizard) an existing ID is refused (409)."""
    if body.asset_type not in ASSET_TYPE_CODES:
        raise HTTPException(400, f"asset_type must be one of {', '.join(ASSET_TYPE_CODES)}")
    row = body.model_dump()
    row["currency"] = row["currency"].strip().upper()
    existing = db.get_instruments()
    match = existing[existing["instrument_id"] == row["instrument_id"].strip()]
    if create and not match.empty:
        raise HTTPException(409, f"A holding with the ID {row['instrument_id'].strip()} already exists. Choose another ID.")
    if not match.empty and db.count_lots(row["instrument_id"].strip()):
        old = match.iloc[0]
        # Purchases are stored in the holding's currency and price convention; changing either would
        # silently reinterpret every purchase.
        if old["currency"] != row["currency"] or float(old["price_divisor"]) != float(row["price_divisor"]):
            raise HTTPException(409, "This holding has purchases: its currency and price divisor cannot change.")
    db.upsert_instrument(row)
    await hub.recompute()
    return {"ok": True}


@app.delete("/api/instruments/{instrument_id}")
async def instrument_delete(instrument_id: str, confirm_purchases: int = Query(0, ge=0),
                            user: str = Depends(auth.require_user)):
    lots = db.count_lots(instrument_id)
    if lots != confirm_purchases:
        raise HTTPException(409, f"This also deletes {lots} purchase(s) and all stored prices. Confirm with confirm_purchases={lots}.")
    db.delete_instrument(instrument_id)
    await hub.recompute()
    return {"ok": True}


@app.get("/api/instruments/{instrument_id}")
async def instrument_detail(instrument_id: str, user: str = Depends(auth.require_user)):
    items = db.get_instruments()
    match = items[items["instrument_id"] == instrument_id]
    if match.empty:
        raise HTTPException(404, "No such holding.")
    item = match.iloc[0].to_dict()
    history = db.get_prices(instrument_id)
    metrics = analyze_history(item, history)
    scenarios = [] if item["asset_type"] == "government_bond" else bootstrap_scenarios(instrument_id, history)
    row = next((r for r in (hub.snapshot or {}).get("rows", []) if r["id"] == instrument_id), None)
    prices = [{"date": d.date().isoformat(), "close": float(c)} for d, c in zip(history["date"], history["close"])]
    return jsonable({
        "instrument": item, "position": row, "metrics": metrics, "scenarios": scenarios, "prices": prices,
        "price_currency": latest_price_currency(history), "lots": db.get_lots(instrument_id).to_dict("records"),
    })


# ---------------------------------------------------------------- purchases
class LotBody(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=64)
    buy_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    quantity: float = Field(gt=0)
    unit_price: float = Field(default=0, ge=0)
    paid_amount: float | None = Field(default=None, ge=0)
    paid_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    notes: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=60)


@app.get("/api/lots")
def lots(user: str = Depends(auth.require_user)):
    return jsonable(db.get_lots().to_dict("records"))


@app.post("/api/lots")
async def lot_add(body: LotBody, user: str = Depends(auth.require_user)):
    frame = pd.DataFrame([{**body.model_dump(), "paid_amount": "" if body.paid_amount is None else str(body.paid_amount),
                           "paid_currency": body.paid_currency or "",
                           "quantity": str(body.quantity), "unit_price": str(body.unit_price)}])
    try:
        rows = validate_lots(frame, set(db.get_instruments()["instrument_id"].astype(str)), db.base_currency())
    except ValueError as exc:
        raise HTTPException(400, str(exc).removeprefix("CSV line 2: ")) from None
    added, skipped = db.add_lots(rows)
    if skipped:
        raise HTTPException(409, "An identical purchase is already recorded.")
    await hub.recompute()
    return {"ok": True}


@app.get("/api/platforms")
def platforms(user: str = Depends(auth.require_user)):
    """Platforms used before, most recent first: suggestions in the add-holding wizard."""
    return db.get_platforms()


@app.get("/api/lookup")
async def lookup(q: str = Query(min_length=2, max_length=60), user: str = Depends(auth.require_user)):
    """Add-holding wizard: find an instrument by name, ticker or ISIN (Yahoo; Hungarian fund ISINs too)."""
    if len(q.strip()) < 2:
        raise HTTPException(422, "Type at least 2 characters.")
    try:
        return await asyncio.to_thread(search_instruments, q)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None




def _quote_out(price: float, raw_ccy: str | None, as_of: str | None, source: str) -> dict:
    """{price (as quoted), currency (major), quote_currency (as quoted), divisor, as_of, source}."""
    raw = (raw_ccy or "").strip()
    major, divisor = MINOR_UNITS.get(raw, (raw.upper() or None, 1.0))
    return {"price": price, "currency": major, "quote_currency": raw or None, "divisor": divisor,
            "as_of": as_of, "source": source}


@app.get("/api/quote")
async def quote(symbol: str = Query(min_length=1, max_length=64), user: str = Depends(auth.require_user)):
    """Price preview for a symbol before it is saved (see _quote_out)."""
    symbol = symbol.strip()
    isin = fund_isin(symbol)
    if isin:
        start = (pd.Timestamp.today() - pd.Timedelta(days=21)).date().isoformat()
        frame, source, error = await asyncio.to_thread(download_fund_nav, isin, start)
        if error or frame.empty:
            raise HTTPException(404, error or f"No recent price for {isin}.")
        last = frame.sort_values("date").iloc[-1]
        return _quote_out(float(last["close"]), str(last["currency"]), pd.Timestamp(last["date"]).date().isoformat(), source)
    if not is_yahoo_symbol(symbol):
        raise HTTPException(400, "Use a Yahoo ticker such as VWCE.DE, or fund:<ISIN> for a Hungarian fund.")
    q = await asyncio.to_thread(live_quote, symbol)
    if not q.get("price"):
        raise HTTPException(404, q.get("error") or f"No price for {symbol}.")
    when = q.get("time")
    return _quote_out(float(q["price"]), q.get("currency"), pd.Timestamp(when).isoformat() if when is not None else None,
                      "Yahoo Finance")


@app.delete("/api/lots/{lot_id}")
async def lot_delete(lot_id: int, instrument_id: str = Query(...), user: str = Depends(auth.require_user)):
    found = db.get_lots(instrument_id)
    if lot_id not in set(found["lot_id"].astype(int)):
        raise HTTPException(404, "That purchase does not belong to this holding (it may have been deleted already).")
    db.delete_lot(lot_id)
    await hub.recompute()
    return {"ok": True}


# ---------------------------------------------------------------- imports + templates
async def _read_csv(file: UploadFile) -> pd.DataFrame:
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 5 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "Save the CSV as UTF-8.") from None
    try:
        return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except Exception as exc:
        raise HTTPException(400, f"Could not read the CSV: {exc}") from None


@app.post("/api/import/holdings")
async def import_holdings(file: UploadFile = File(...), user: str = Depends(auth.require_user)):
    frame = (await _read_csv(file)).replace("", None)
    try:
        rows = validate_holdings(frame)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    count = db.upsert_instruments(rows)
    await hub.refresh()
    return {"imported": count}


@app.post("/api/import/lots")
async def import_lots(file: UploadFile = File(...), user: str = Depends(auth.require_user)):
    frame = await _read_csv(file)
    try:
        rows = validate_lots(frame, set(db.get_instruments()["instrument_id"].astype(str)), db.base_currency())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    added, skipped = db.add_lots(rows)
    await hub.recompute()
    return {"added": added, "skipped_duplicates": skipped}


@app.post("/api/import/prices")
async def import_prices(file: UploadFile = File(...), user: str = Depends(auth.require_user)):
    frame = await _read_csv(file)
    missing = {"instrument_id", "date", "close"} - set(frame.columns)
    if missing:
        raise HTTPException(400, "Missing columns: " + ", ".join(sorted(missing)) + ". First line: instrument_id,date,close,currency")
    known = set(db.get_instruments()["instrument_id"].astype(str))
    unknown = set(frame["instrument_id"].str.strip()) - known
    if unknown:
        raise HTTPException(400, "Import these holdings first: " + ", ".join(sorted(unknown)))
    if "currency" not in frame.columns:
        frame["currency"] = None
    frame["currency"] = frame["currency"].replace("", None)
    total = 0
    try:
        frame["close"] = pd.to_numeric(frame["close"].str.replace(",", ".", regex=False), errors="raise")
        for iid, group in frame.groupby(frame["instrument_id"].str.strip()):
            total += db.save_prices(iid, group[["date", "close", "currency"]], "CSV import")
    except Exception as exc:
        raise HTTPException(400, f"Could not import prices: {exc}") from None
    await hub.recompute()
    return {"imported": total}


TEMPLATES = {
    "holdings": "instrument_id,name,asset_type,quantity,avg_cost,currency,market_symbol,price_divisor,notes\n",
    "lots": LOTS_HEADER + "\n",
    "prices": "instrument_id,date,close,currency\n",
}


@app.get("/api/templates/{kind}.csv")
def template(kind: Literal["holdings", "lots", "prices"], user: str = Depends(auth.require_user)):
    return Response(TEMPLATES[kind], media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{kind}_template.csv"'})


@app.get("/api/backup")
def backup(user: str = Depends(auth.require_user)):
    return Response(db.backup_bytes(), media_type="application/x-sqlite3",
                    headers={"Content-Disposition": 'attachment; filename="investment_analyzer_backup.sqlite3"'})


# ---------------------------------------------------------------- Claude
class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=1024)


@app.get("/api/claude/status")
async def claude_status(user: str = Depends(auth.require_user)):
    return await asyncio.to_thread(claude_login.status)


@app.post("/api/claude/login")
async def claude_login_start(user: str = Depends(auth.require_user)):
    try:
        return {"url": await asyncio.to_thread(claude_login.start)}
    except (claude_login.LoginError, ImportError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/claude/code")
async def claude_login_code(body: CodeBody, user: str = Depends(auth.require_user)):
    try:
        return await asyncio.to_thread(claude_login.submit, body.code)
    except claude_login.LoginError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/claude/logout")
async def claude_logout(user: str = Depends(auth.require_user)):
    return await asyncio.to_thread(claude_login.logout)


# ---------------------------------------------------------------- AI advisor (any model)
class ProviderBody(BaseModel):
    id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["claude_code", "anthropic", "openai_compat"]
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(default="", max_length=300)
    data_level: Literal["relative", "full"] = "relative"
    web_research: bool = False
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    api_key: str | None = Field(default=None, max_length=500)
    clear_key: bool = False


class SettingsBody(BaseModel):
    persona: str | None = Field(default=None, max_length=20000)
    preferences: dict | None = None
    # Standing rules for the advisor; validated here so an over-long list is a clear 422, not a silent cut.
    rules: list[Annotated[str, Field(max_length=MAX_RULE_CHARS)]] | None = Field(default=None, max_length=MAX_RULES)
    # the list the client last saw: a mismatch answers 409 instead of silently overwriting another device's edit
    rules_expected: list[Annotated[str, Field(max_length=MAX_RULE_CHARS)]] | None = Field(default=None, max_length=MAX_RULES)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=advisor.MAX_QUESTION_CHARS)
    history: list[dict] = Field(default_factory=list, max_length=40)
    provider_id: str | None = Field(default=None, max_length=32)
    instrument_id: str | None = Field(default=None, max_length=64)


@app.get("/api/ai/presets")
def ai_presets(user: str = Depends(auth.require_user)):
    return llm.PRESETS


@app.get("/api/ai/providers")
def ai_providers(user: str = Depends(auth.require_user)):
    return advisor.list_providers()


@app.post("/api/ai/providers")
def ai_provider_save(body: ProviderBody, user: str = Depends(auth.require_user)):
    try:
        pid = advisor.save_provider(body.model_dump(exclude={"api_key", "clear_key"}), body.api_key)
    except (llm.LLMError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None
    if body.clear_key:
        from analyzer import secrets_store
        secrets_store.delete(f"provider:{pid}")
    return {"id": pid}


@app.delete("/api/ai/providers/{provider_id}")
def ai_provider_delete(provider_id: str, user: str = Depends(auth.require_user)):
    try:
        advisor.delete_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


class DataLevelBody(BaseModel):
    data_level: Literal["relative", "full"]
    # "full" sends amounts, quantities and purchase dates to the model: the client must confirm it explicitly
    confirm_full: bool = False


@app.post("/api/ai/providers/{provider_id}/data-level")
def ai_provider_data_level(provider_id: str, body: DataLevelBody, user: str = Depends(auth.require_user)):
    if body.data_level == "full" and not body.confirm_full:
        raise HTTPException(422, "Sharing full detail needs an explicit confirmation.")
    try:
        advisor.set_data_level(provider_id, body.data_level)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    log.info("AI model %s data level set to %s by %s", provider_id, body.data_level, user)
    return {"ok": True, "data_level": body.data_level}


@app.post("/api/ai/providers/{provider_id}/default")
def ai_provider_default(provider_id: str, user: str = Depends(auth.require_user)):
    try:
        advisor.set_default(provider_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    return {"ok": True}


@app.post("/api/ai/providers/{provider_id}/test")
async def ai_provider_test(provider_id: str, user: str = Depends(auth.require_user)):
    """A tiny request with no portfolio data, to check the model, key and URL."""
    try:
        provider = advisor.get_provider(provider_id)
        result = await asyncio.to_thread(
            llm.complete, provider, "You are a connection test. Reply with exactly: OK",
            [{"role": "user", "content": "Reply with exactly: OK"}])
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"ok": True, "model": result["model"], "reply": result["text"][:200]}


@app.get("/api/ai/settings")
def ai_settings(user: str = Depends(auth.require_user)):
    return advisor.get_settings()


@app.put("/api/ai/settings")
def ai_settings_save(body: SettingsBody, user: str = Depends(auth.require_user)):
    if body.rules is not None and body.rules_expected is None:
        raise HTTPException(422, "Send rules_expected (the list you are replacing) with rules.")
    try:
        return advisor.save_settings(body.persona, body.preferences, body.rules, body.rules_expected)
    except advisor.RulesConflict as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/advisor/ask")
async def advisor_ask(body: AskBody, user: str = Depends(auth.require_user)):
    snapshot = hub.snapshot or await hub.refresh()
    if body.instrument_id and not any(r["id"] == body.instrument_id for r in snapshot.get("rows", [])):
        raise HTTPException(404, "No such holding.")
    try:
        result = await asyncio.to_thread(advisor.ask, body.provider_id, body.question, body.history, snapshot,
                                         body.instrument_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, llm.LLMError) as exc:
        raise HTTPException(502 if isinstance(exc, llm.LLMError) else 400, str(exc)) from None
    return jsonable(result)


@app.get("/api/research")
async def research_overview(user: str = Depends(auth.require_user)):
    market, news = await asyncio.gather(asyncio.to_thread(research.market_context),
                                        asyncio.to_thread(research.recent_news, research.NEWS_DAYS, 40))
    return jsonable({"market": market, "news": news, "feeds": [{"name": n, "url": u} for n, u in research.DEFAULT_FEEDS]})

# ---------------------------------------------------------------- the React app
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        logo = STATIC_DIR / "logo.svg"
        if path == "favicon.ico" and not (STATIC_DIR / path).is_file() and logo.is_file():
            # browsers ask for it on their own (e.g. a JSON tab); the logo is the icon
            return FileResponse(logo, media_type="image/svg+xml")
        candidate = (STATIC_DIR / path).resolve()
        if path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
