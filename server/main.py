"""HTTP API + live stream + the built React app, on one port.

Run: uvicorn server.main:app --host 0.0.0.0 --port 8080   (the Docker image does this)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import pandas as pd
from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from analyzer import advisor, attachments, db, llm, prefs, proposals, research, symbols
from analyzer.persona import MAX_RULE_CHARS, MAX_RULES
from analyzer.engine import analyze_history, bootstrap_scenarios, latest_price_currency, realized_by_sale
from analyzer.portfolio import RateBook, intraday_value_history, value_history
from analyzer.risk import risk_report
from analyzer.market import COMMON_CURRENCIES, CURRENCY_RE, download_intraday, fx_symbol, live_quote, search_instruments
from analyzer.portfolio import is_yahoo_symbol
from analyzer.validation import ASSET_TYPE_CODES, HOLDINGS_HEADER, LOTS_HEADER, validate_holdings, validate_lots
from server import __version__, auth, claude_login
from server.live import LIVE_CHOICES, clean, hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lia")
ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(os.environ.get("LIA_STATIC_DIR") or ROOT / "frontend" / "dist")
COOKIE_SECURE = os.environ.get("LIA_COOKIE_SECURE") == "1"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# Request body limits (BodyLimit below), picked by path, never from a client header. Most bodies are small
# JSON; an AI question carries up to 24 earlier messages, whose answers can be long; CSV uploads are
# MAX_UPLOAD_BYTES plus multipart overhead.
MAX_JSON_BODY = 1024 * 1024
# the 4 MB the history always had, plus up to 6 MB of attachments base64-encoded (8 MB) and JSON overhead
MAX_ASK_BODY = 13 * 1024 * 1024
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


def _body_limit(path: str, headers: dict) -> int:
    """The larger limits also need a session cookie: those routes refuse anyone without one anyway, and an
    anonymous client must not make the server parse megabytes before the login check runs."""
    signed_in = f"{auth.COOKIE}=".encode() in headers.get(b"cookie", b"")
    if signed_in and path.startswith("/api/import/"):
        return MAX_MULTIPART_BODY
    if signed_in and path == "/api/advisor/ask":
        return MAX_ASK_BODY
    return MAX_JSON_BODY


class BodyLimit:
    """Refuses oversized request bodies with 413: by Content-Length before the app runs at all (so an
    endpoint that reads no body, such as a DELETE, never acts on a refused request), and by counting a
    chunked body as it streams in. FastAPI's own max_body_size is not used because it only checks once
    the endpoint reads the body or answers, after any side effect."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        limit = _body_limit(scope.get("path", ""), headers)
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = True
            if too_big:
                return await self._refuse(send)
        seen = 0
        started = refused = answered = False

        async def counted():
            # Past the limit the app is told the client left (an exception here would become FastAPI's
            # own 400). The 413 goes out only if no response has begun: a stream cannot turn into one.
            nonlocal seen, refused, answered
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    refused = True
                    if not started:
                        answered = True
                        await self._refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal started
            if answered:  # after our 413 nothing the app answers may reach the client
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, counted, guarded_send)

    @staticmethod
    async def _refuse(send):
        body = b'{"detail":"That is too much to send at once."}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


app.add_middleware(BodyLimit)  # added before security_headers, so its 413 gets those headers too


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
    # a new install's reporting currency, from the browser's region; ignored unless it is a listed one
    base_currency: str | None = Field(default=None, max_length=3)


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
    code = (body.base_currency or "").strip().upper()
    if code in COMMON_CURRENCIES and db.get_setting("base_currency") is None:
        db.set_setting("base_currency", code)
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
# Intraday intervals each chart range offers (holding and portfolio charts): only what Yahoo serves for that
# span (1-minute bars for 7 days, up to 30 minutes for 60, hourly for 730). Daily ("1d") is the stored prices.
CHART_INTERVALS = {"1W": ("1m", "5m", "15m", "30m", "1h"), "1M": ("5m", "15m", "30m", "1h", "4h"),
                   "3M": ("1h", "4h"), "6M": ("1h", "4h"), "1Y": ("1h", "4h")}


@app.get("/api/portfolio/history")
async def portfolio_history(range: Literal["1W", "1M", "3M", "6M", "1Y", "3Y", "ALL"] = "1Y",
                            interval: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"] = "1d",
                            user: str = Depends(auth.require_user)):
    """Daily (stored) by default; a minute/hour interval rebuilds the total from Yahoo's bars when asked."""
    if interval == "1d":
        return jsonable({"range": range, "interval": interval, **await asyncio.to_thread(value_history, RANGE_DAYS[range], db.base_currency())})
    if interval not in CHART_INTERVALS.get(range, ()):
        raise HTTPException(400, f"Yahoo has no {interval} prices for a {range} chart.")
    result = await asyncio.to_thread(intraday_value_history, RANGE_DAYS[range], interval, db.base_currency())
    if result.get("error"):
        raise HTTPException(502 if "Yahoo returned" in result["error"] else 400, result["error"])
    return jsonable({"range": range, "interval": interval, **result})


@app.get("/api/risk")
async def risk(range: Literal["1Y", "3Y"] = "1Y", user: str = Depends(auth.require_user)):
    """Concentration, exposure and the history of today's mix, from stored data (on a cold start only the
    portfolio snapshot is built first, which may fetch quotes, as for /api/portfolio)."""
    snapshot = hub.snapshot or await hub.refresh()
    return jsonable({"range": range, **await asyncio.to_thread(risk_report, snapshot, RANGE_DAYS[range])})


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


class AppSettingsBody(BaseModel):
    base_currency: str = Field(min_length=3, max_length=3)


def _app_settings() -> dict:
    return {"base_currency": db.base_currency(), "currencies": COMMON_CURRENCIES,
            "live_seconds": hub.seconds(), "live_choices": list(LIVE_CHOICES)}


@app.get("/api/settings")
def app_settings(user: str = Depends(auth.require_user)):
    return _app_settings()


class LiveSettingsBody(BaseModel):
    seconds: int


@app.put("/api/settings/live")
async def live_settings_save(body: LiveSettingsBody, user: str = Depends(auth.require_user)):
    """How often live prices refresh while a browser is open; applies at once (async: it wakes the live loop)."""
    try:
        hub.set_seconds(body.seconds)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return _app_settings()


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
    return _app_settings()


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
class FirstBuy(BaseModel):
    """A new holding's first buy, stored with it in one step (?create=1 only)."""
    buy_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    quantity: float = Field(gt=0)
    unit_price: float = Field(default=0, ge=0)
    paid_amount: float | None = Field(default=None, ge=0)
    paid_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    notes: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=60)


class InstrumentBody(BaseModel):
    """A holding's description. No units or cost: those come only from its buys and sells (owner decision
    2026-11-26); a client that still sends quantity or avg_cost has them ignored."""
    instrument_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    asset_type: str
    currency: str = Field(min_length=3, max_length=3)
    market_symbol: str | None = Field(default=None, max_length=64)
    price_divisor: float = Field(default=1.0, gt=0)
    notes: str | None = Field(default=None, max_length=1000)
    buy: FirstBuy | None = None


@app.get("/api/instruments")
def instruments(user: str = Depends(auth.require_user)):
    """Every holding; `record_only`: its units are on its record without transactions (an old holding, to be
    turned into a buy with POST /api/instruments/{id}/record-to-buy)."""
    old = db.record_only_ids()
    return jsonable([{**r, "record_only": r["instrument_id"] in old} for r in db.get_instruments().to_dict("records")])


@app.post("/api/instruments")
async def instrument_save(body: InstrumentBody, create: bool = False, update: bool = False,
                          user: str = Depends(auth.require_user)):
    """Add or update a holding. With ?create=1 (the add-holding wizard) an existing ID is refused (409);
    with ?update=1 (the edit form) a missing one is (404), so a holding deleted on another device is
    not silently re-created from a stale form."""
    if body.asset_type not in ASSET_TYPE_CODES:
        raise HTTPException(400, f"asset_type must be one of {', '.join(ASSET_TYPE_CODES)}")
    if body.buy is not None and not create:
        raise HTTPException(400, "A first buy can only come with a new holding (?create=1); add transactions otherwise.")
    row = body.model_dump(exclude={"buy"})
    row["currency"] = row["currency"].strip().upper()
    existing = db.get_instruments()
    match = existing[existing["instrument_id"] == row["instrument_id"].strip()]
    if create and not match.empty:
        raise HTTPException(409, f"A holding with the ID {row['instrument_id'].strip()} already exists. Choose another ID.")
    if update and match.empty:
        raise HTTPException(404, "This holding no longer exists. It may have been deleted on another device.")
    if not match.empty and db.count_lots(row["instrument_id"].strip()):
        old = match.iloc[0]
        # Purchases are stored in the holding's currency and price convention; changing either would
        # silently reinterpret every purchase.
        if old["currency"] != row["currency"] or float(old["price_divisor"]) != float(row["price_divisor"]):
            raise HTTPException(409, "This holding has transactions: its currency and price divisor cannot change.")
    if body.buy is not None:
        lot = body.buy.model_dump()
        frame = pd.DataFrame([{**lot, "instrument_id": row["instrument_id"].strip(), "side": "buy",
                               "paid_amount": "" if lot["paid_amount"] is None else str(lot["paid_amount"]),
                               "paid_currency": lot["paid_currency"] or "", "quantity": str(lot["quantity"]),
                               "unit_price": str(lot["unit_price"])}])
        try:
            (checked,) = validate_lots(frame, {row["instrument_id"].strip()}, db.base_currency())
        except ValueError as exc:
            raise HTTPException(400, str(exc).removeprefix("CSV line 2: ")) from None
        try:
            db.create_instrument_with_buy(row, checked)
        except sqlite3.IntegrityError:  # created meanwhile (another tab)
            raise HTTPException(409, f"A holding with the ID {row['instrument_id'].strip()} already exists. Choose another ID.") from None
    else:
        db.save_instrument(row)
    await hub.recompute()
    raw_old = None if match.empty else match.iloc[0]["market_symbol"]
    old_symbol = None if match.empty else ("" if pd.isna(raw_old) else str(raw_old).strip())  # a stored NULL reads as NaN
    if old_symbol != str(row.get("market_symbol") or "").strip():  # new, or another symbol: not a notes edit
        hub.request_refresh(row["instrument_id"].strip())  # its quote and daily history now, not an interval later
    return {"ok": True}


class RecordToBuyBody(BaseModel):
    buy_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    unit_price: float | None = Field(default=None, gt=0)
    platform: str | None = Field(default=None, max_length=60)


@app.post("/api/instruments/{instrument_id}/record-to-buy")
async def instrument_record_to_buy(instrument_id: str, body: RecordToBuyBody, user: str = Depends(auth.require_user)):
    """Turn an old holding's recorded units into one buy on the date the owner picks (a one-time step: from then on
    its units and cost come from transactions only)."""
    try:
        day = pd.Timestamp(body.buy_date)
    except ValueError:
        raise HTTPException(400, "The date is not a real date.") from None
    if day > pd.Timestamp.today().normalize() or day.year < 1900:
        raise HTTPException(400, "The date must be a past date.")
    if body.unit_price is not None and not math.isfinite(body.unit_price):  # as POST /api/lots refuses it
        raise HTTPException(400, "The price per unit must be a finite number.")
    try:
        db.record_to_buy(instrument_id, body.buy_date, body.unit_price, (body.platform or "").strip() or None)
    except db.NothingToConvert as exc:
        raise HTTPException(409, str(exc)) from None
    await hub.recompute()
    return {"ok": True}


@app.delete("/api/instruments/{instrument_id}")
async def instrument_delete(instrument_id: str, confirm_purchases: int = Query(0, ge=0),
                            user: str = Depends(auth.require_user)):
    lots = db.count_lots(instrument_id)
    if lots != confirm_purchases:
        raise HTTPException(409, f"This also deletes {lots} transaction(s) and all stored prices. Confirm with confirm_purchases={lots}.")
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
    lots = db.get_lots(instrument_id).to_dict("records")
    base = db.base_currency()
    # realized P/L of each sale (base currency; null when something it needs is unknown), for the transactions table
    realized = realized_by_sale(item, lots, RateBook(base).rate, base)
    for lot in lots:
        lot["realized_base"] = realized.get(lot["lot_id"])
    return jsonable({
        "instrument": item, "position": row, "metrics": metrics, "scenarios": scenarios, "prices": prices,
        "price_currency": latest_price_currency(history), "lots": lots,
    })


@app.get("/api/instruments/{instrument_id}/chart")
async def instrument_chart(instrument_id: str, range: Literal["1W", "1M", "3M", "6M", "1Y"],
                           interval: Literal["1m", "5m", "15m", "30m", "1h", "4h"], user: str = Depends(auth.require_user)):
    """Intraday prices for one holding's chart, fetched from Yahoo when asked (never stored)."""
    if interval not in CHART_INTERVALS[range]:
        raise HTTPException(400, f"Yahoo has no {interval} prices for a {range} chart.")
    items = db.get_instruments()
    match = items[items["instrument_id"] == instrument_id]
    if match.empty:
        raise HTTPException(404, "No such holding.")
    symbol = match.iloc[0]["market_symbol"]
    if not is_yahoo_symbol(symbol):
        raise HTTPException(400, "Only daily prices are available for this holding.")
    frame, currency, error = await asyncio.to_thread(download_intraday, str(symbol).strip(), interval, RANGE_DAYS[range])
    if error:
        raise HTTPException(502, error)
    points = [{"t": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "close": float(c)} for ts, c in zip(frame["ts"], frame["close"])]
    return jsonable({"range": range, "interval": interval, "currency": currency, "points": points})


# ---------------------------------------------------------------- transactions (buys and sells; stored in `lots`)
class LotBody(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=64)
    side: Literal["buy", "sell"] = "buy"
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
    try:
        added, skipped = db.add_lots(rows)
    except db.SaleTooLarge as exc:
        raise HTTPException(400, str(exc)) from None
    if skipped:
        raise HTTPException(409, "An identical transaction is already recorded.")
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




@app.get("/api/quote")
async def quote(symbol: str = Query(min_length=1, max_length=64), user: str = Depends(auth.require_user)):
    """Price preview for a symbol before it is saved (analyzer/symbols.py; AI "add a holding" cards use it too)."""
    try:
        return await asyncio.to_thread(symbols.quote_symbol, symbol)
    except symbols.SymbolError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@app.delete("/api/lots/{lot_id}")
async def lot_delete(lot_id: int, instrument_id: str = Query(...), user: str = Depends(auth.require_user)):
    found = db.get_lots(instrument_id)
    if lot_id not in set(found["lot_id"].astype(int)):
        raise HTTPException(404, "That transaction does not belong to this holding (it may have been deleted already).")
    try:
        db.delete_lot(lot_id)
    except db.LotInUse as exc:
        raise HTTPException(409, str(exc)) from None
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
    # like the edit form: a holding with transactions keeps its currency and price divisor (they say how every
    # stored transaction is read)
    stored = {r["instrument_id"]: r for r in db.get_instruments().to_dict("records")}
    for line, row in enumerate(rows, start=2):
        old = stored.get(row["instrument_id"])
        if old and db.count_lots(row["instrument_id"]) and (
                old["currency"] != row["currency"] or float(old["price_divisor"]) != float(row["price_divisor"])):
            raise HTTPException(400, f"CSV line {line}: {row['instrument_id']} has transactions: its currency and price "
                                     "divisor cannot change.")
    count = db.save_instruments(rows)  # never units or cost: those come from the transactions file
    await hub.refresh()
    return {"imported": count}


@app.post("/api/import/lots")
async def import_lots(file: UploadFile = File(...), user: str = Depends(auth.require_user)):
    frame = await _read_csv(file)
    try:
        rows = validate_lots(frame, set(db.get_instruments()["instrument_id"].astype(str)), db.base_currency())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        added, skipped = db.add_lots(rows)
    except db.SaleTooLarge as exc:  # nothing was stored; `row` indexes the file's data lines (line 1 is the header)
        raise HTTPException(400, f"CSV line {exc.row + 2}: {exc}") from None
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
    "holdings": HOLDINGS_HEADER + "\n",
    "lots": LOTS_HEADER + "\n",
    "prices": "instrument_id,date,close,currency\n",
}


@app.get("/api/templates/{kind}.csv")
def template(kind: Literal["holdings", "lots", "prices"], user: str = Depends(auth.require_user)):
    return Response(TEMPLATES[kind], media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{kind}_template.csv"'})


@app.get("/api/backup")
def backup(user: str = Depends(auth.require_user)):
    advisor.purge_conversations()  # expired AI conversations never reach a backup
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
        url = await asyncio.to_thread(claude_login.start)
        # No URL yet: Claude Code is downloading in the background; the UI polls /api/claude/status.
        return {"url": url} if url else {"installing": True}
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
# An API key is printable ASCII without inner spaces (outer ones are trimmed); anything else would end
# up in an HTTP header, where urllib raises and the traceback would log the key.
KEY_PATTERN = r"^\s*[\x21-\x7e]+\s*$"


class ProviderBody(BaseModel):
    id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["claude_code", "anthropic", "openai_compat"]
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(default="", max_length=300)
    data_level: Literal["relative", "full"] = "relative"  # ignored: set only via /data-level (full needs confirm)
    web_research: bool = False
    effort: Literal["default", "low", "medium", "high", "xhigh", "max"] = "high"
    api_key: str | None = Field(default=None, max_length=500, pattern=KEY_PATTERN)
    clear_key: bool = False


class ModelListBody(BaseModel):
    kind: Literal["anthropic", "openai_compat"]
    base_url: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, max_length=500, pattern=KEY_PATTERN)
    # an existing model whose saved key is used when no key is typed
    provider_id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


class SettingsBody(BaseModel):
    persona: str | None = Field(default=None, max_length=20000)
    preferences: dict | None = None
    # Standing rules for the advisor; validated here so an over-long list is a clear 422, not a silent cut.
    rules: list[Annotated[str, Field(max_length=MAX_RULE_CHARS)]] | None = Field(default=None, max_length=MAX_RULES)
    # the list the client last saw: a mismatch answers 409 instead of silently overwriting another device's edit
    rules_expected: list[Annotated[str, Field(max_length=MAX_RULE_CHARS)]] | None = Field(default=None, max_length=MAX_RULES)


class AttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=9 * 1024 * 1024)  # base64; "" is an empty file (it becomes an empty text part)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=advisor.MAX_QUESTION_CHARS)
    history: list[dict] = Field(default_factory=list, max_length=40)
    provider_id: str | None = Field(default=None, max_length=32)
    instrument_id: str | None = Field(default=None, max_length=64)
    # the level the conversation's answers were written at: a model whose level changed meanwhile gets a 409
    expected_level: Literal["relative", "full"] | None = None
    # this question only (chosen in the chat); the model's saved effort when omitted
    effort: Literal["default", "low", "medium", "high", "xhigh", "max"] | None = None
    # the saved conversation this question and its answer are added to (made by the browser)
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,40}$")
    # the browser's id for this question: a retry replaces the saved answer instead of adding it twice
    turn_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,40}$")
    # files for this question only (base64); sniffed and converted per model in analyzer.attachments
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=attachments.MAX_FILES)


@app.get("/api/ai/presets")
def ai_presets(user: str = Depends(auth.require_user)):
    return llm.PRESETS


@app.get("/api/ai/providers")
def ai_providers(user: str = Depends(auth.require_user)):
    return advisor.list_providers()


@app.post("/api/ai/models")
def ai_models(body: ModelListBody, user: str = Depends(auth.require_user)):
    """The provider's model list for the Model suggestions; asked for with a button, never automatically."""
    from analyzer import secrets_store
    base_url = body.base_url.strip()
    key = (body.api_key or "").strip() or None
    if key is None and body.provider_id:
        try:
            saved = advisor.get_provider(body.provider_id)
        except KeyError:
            raise HTTPException(404, "No such AI model.") from None
        # A saved key only ever goes where it was saved for: an edited URL or type needs the key typed again.
        if saved.kind != body.kind or saved.base_url.strip().rstrip("/") != base_url.rstrip("/"):
            raise HTTPException(400, "The base URL differs from the saved one: type the API key again to list models there.")
        key = secrets_store.get(saved.secret_name)
    try:
        return llm.list_models(body.kind, base_url, key)
    except llm.LLMError as exc:
        raise HTTPException(400, str(exc)) from None


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
    # "full" sends amounts, quantities, costs, realized P/L, purchase and sale dates, platforms and holding notes: the client must confirm it
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
    snapshot = hub.snapshot
    if snapshot is None or hub.quotes_age() > 2 * hub.seconds():  # after a pause with nobody watching: refresh first
        try:
            snapshot = await hub.refresh(manual=True) or snapshot
        except Exception:
            if snapshot is None:
                raise
            log.warning("could not refresh prices before an AI question; using the last snapshot")
    if body.instrument_id and not any(r["id"] == body.instrument_id for r in snapshot.get("rows", [])):
        raise HTTPException(404, "No such holding.")
    try:
        files = attachments.decode([a.model_dump() for a in body.attachments])
    except attachments.AttachmentError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        result = await asyncio.to_thread(advisor.ask, body.provider_id, body.question, body.history, snapshot,
                                         body.instrument_id, expected_level=body.expected_level, effort=body.effort,
                                         conversation_id=body.conversation_id, turn_id=body.turn_id, files=files)
    except advisor.LevelChanged as exc:
        raise HTTPException(409, str(exc)) from None
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ValueError, llm.LLMError) as exc:
        raise HTTPException(502 if isinstance(exc, llm.LLMError) else 400, str(exc)) from None
    return jsonable(result)


# Saved conversations: questions and answers kept in this server's database for CONVERSATION_KEEP_DAYS.
CONVERSATION_ID = PathParam(pattern=r"^[A-Za-z0-9_-]{8,40}$")


@app.get("/api/advisor/conversations")
def advisor_conversations(user: str = Depends(auth.require_user)):
    return advisor.list_conversations()


@app.get("/api/advisor/conversations/{conversation_id}")
def advisor_conversation(conversation_id: str = CONVERSATION_ID, user: str = Depends(auth.require_user)):
    try:
        return advisor.get_conversation(conversation_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None


@app.delete("/api/advisor/conversations/{conversation_id}")
def advisor_conversation_delete(conversation_id: str = CONVERSATION_ID, user: str = Depends(auth.require_user)):
    if not advisor.delete_conversation(conversation_id):
        raise HTTPException(404, "No such conversation (it may have been deleted).")
    return {"ok": True}


@app.delete("/api/advisor/conversations")
def advisor_conversations_delete_all(user: str = Depends(auth.require_user)):
    return {"deleted": advisor.delete_conversation(None)}


# Change cards (analyzer/proposals.py). The browser applies a card through the app's own endpoints; these two only
# re-check a card against the current settings and record what became of it.
class ProposalPreviewBody(BaseModel):
    type: str = Field(max_length=40)
    payload: dict


class ProposalStatusBody(BaseModel):
    status: Literal["applied", "dismissed", "adjusted"]


@app.post("/api/advisor/proposals/preview")
def advisor_proposal_preview(body: ProposalPreviewBody, user: str = Depends(auth.require_user)):
    """Read-only: the card's before -> after as things stand now, or why it can't be applied any more."""
    return proposals.preview(body.type, body.payload, advisor.proposal_state())


@app.post("/api/advisor/conversations/{conversation_id}/proposals/{proposal_id}")
def advisor_proposal_status(body: ProposalStatusBody, conversation_id: str = CONVERSATION_ID,
                            proposal_id: str = PathParam(pattern=r"^[0-9a-f]{12}$"), user: str = Depends(auth.require_user)):
    try:
        advisor.set_proposal_status(conversation_id, proposal_id, body.status)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    except advisor.ProposalDone as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True}


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
