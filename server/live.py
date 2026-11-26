"""The single place that talks to market data sources, on a fixed cadence, for all browser tabs.

Every live interval (Settings), while a browser is watching: live Yahoo quotes + FX -> portfolio snapshot ->
pushed to SSE subscribers.
Daily: Yahoo daily price history per instrument and FX history per currency.
Hourly: Hungarian fund prices (OTP Alapkezelő / BAMOSZ).
Sources that fail are retried after a back-off, never on every tick.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pandas as pd

import yfinance as yf

from analyzer import db, research
from analyzer.market import (
    download_daily_history, download_fund_nav, download_fx_history, fund_isin, fx_symbol, live_quote,
)
from analyzer.portfolio import build_snapshot, is_yahoo_symbol

log = logging.getLogger("lia.live")
# How often live prices refresh: chosen in Settings (stored as "live_seconds"); LIA_LIVE_SECONDS is only the
# default until a choice is saved. Never under 15 s: Yahoo throttles clients that ask more often.
LIVE_CHOICES = (15, 30, 60, 120, 300, 900)


def _default_seconds() -> int:
    """LIA_LIVE_SECONDS, rounded to the nearest choice so Settings always shows the interval in use."""
    try:
        wanted = int(os.environ.get("LIA_LIVE_SECONDS", "60"))
    except ValueError:
        wanted = 60
    return min(LIVE_CHOICES, key=lambda c: abs(c - wanted))


DEFAULT_LIVE_SECONDS = _default_seconds()
BACKGROUND_SECONDS = 3600  # with nobody watching: daily history, fund NAVs, benchmarks and news, hourly as before
LOOP_ERROR_PAUSE = 15
MIN_QUOTE_GAP = 15  # the shortest interval: a requested refresh after an edit keeps it too
FUND_SECONDS = 3600
FAIL_BACKOFF_SECONDS = 600
MANUAL_MIN_GAP_SECONDS = 30
BENCH_BACKFILL_DAYS = 1132  # a stored benchmark starting later than ~3.1 years ago can't cover the Risk page's 3Y range
READJUST_TOLERANCE = 0.001  # a stored past close differing more than 0.1% from Yahoo's: history re-adjusted
MONTH_COVERS = pd.Timedelta(days=20)  # a one-month download only closes a gap shorter than this; older: in full
YAHOO = "Yahoo Finance via yfinance"
IMPORTED = "CSV import"  # prices the owner imported are theirs: Yahoo fills the other days, never replaces them


def _yahoo_prices(instrument_id: str, since: str | None = None) -> pd.DataFrame:
    stored = db.get_prices(instrument_id, since=since)
    return stored[stored["source"] != IMPORTED] if not stored.empty else stored


def full_period(instrument_id: str) -> tuple[str, str | None]:
    """The shortest Yahoo period that reaches back to the oldest stored Yahoo close (a repair that stops at
    5 years would leave older ones on the old scale), and that date: a repair keeps only rows from it on, so
    the stored history doesn't grow backwards with every start (5y, then 10y, then the whole listing)."""
    stored = _yahoo_prices(instrument_id)
    if stored.empty:
        return "5y", None
    oldest = pd.Timestamp(stored["date"].min()).date().isoformat()
    years = (pd.Timestamp.today() - pd.Timestamp(oldest)).days / 365.25
    return ("5y" if years <= 5.05 else "10y" if years <= 10.05 else "max"), oldest


def newest_yahoo(instrument_id: str) -> str | None:
    stored = _yahoo_prices(instrument_id)
    return None if stored.empty else pd.Timestamp(stored["date"].max()).date().isoformat()


def without_imported(instrument_id: str, frame: pd.DataFrame) -> pd.DataFrame:
    stored = db.get_prices(instrument_id)
    if stored.empty or frame.empty:
        return frame
    mine = {pd.Timestamp(d).date().isoformat() for d in stored.loc[stored["source"] == IMPORTED, "date"]}
    return frame[[pd.Timestamp(d).date().isoformat() not in mine for d in frame["date"]]] if mine else frame


def readjusted(instrument_id: str, frame: pd.DataFrame) -> bool:
    """Yahoo's adjusted closes change for every past day after a split or dividend. True when the fresh closes
    differ from the stored Yahoo ones on the same past days: the older stored closes are then on another scale,
    and only a download over the whole stored range keeps the history free of a fake jump (a 10:1 split read
    as a 90% fall)."""
    if frame.empty:
        return False
    new = pd.Series(frame["close"].to_numpy(dtype=float), index=[pd.Timestamp(d).date().isoformat() for d in frame["date"]])
    stored = _yahoo_prices(instrument_id, since=new.index.min())
    if stored.empty:
        return False
    old = pd.Series(stored["close"].to_numpy(dtype=float), index=[pd.Timestamp(d).date().isoformat() for d in stored["date"]])
    both = pd.concat([old, new], axis=1, keys=["old", "new"]).dropna().sort_index().iloc[:-1]  # newest: maybe mid-session
    return bool(((both["new"] / both["old"] - 1).abs() > READJUST_TOLERANCE).any()) if not both.empty else False


def clean(obj):
    """JSON-safe copy: NaN/inf -> None, timestamps -> ISO strings."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    return obj


class LiveHub:
    def __init__(self) -> None:
        self.snapshot: dict | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        # monotonic clocks: when quotes / the history jobs last ran, and the earliest retry after a failed cycle
        self._last_quotes = float("-inf")
        self._last_background = float("-inf")
        self._retry_at = float("-inf")
        self._quotes_at: datetime | None = None  # wall time of the last live quotes, for the snapshot's stamp
        self._daily_done: dict[str, str] = {}     # key -> ISO day of last success
        self._next_try: dict[str, float] = {}     # key -> earliest retry time after a failure
        self._fund_done: dict[str, float] = {}
        self._bench_backfilled: set[str] = set()  # benchmarks given a 5y download this run (once is enough)
        self._daily_full: set[str] = set()  # holdings whose daily history was downloaded in full (5y) this run
        self._errors: dict[str, str] = {}         # symbol / "FX EUR" / "FX history EUR" -> message, cleared on success
        self._quotes: dict[str, dict] = {}        # last live quotes, reused by recompute()
        self._wake = asyncio.Event()              # a viewer arrived or the interval changed: re-plan the loop

    # ---- interval ------------------------------------------------------
    def seconds(self) -> int:
        """Never raises: the loop calls it every pass, and a locked database must not end live prices."""
        try:
            value = int(db.get_setting("live_seconds") or 0)
        except Exception:
            return DEFAULT_LIVE_SECONDS
        return value if value in LIVE_CHOICES else DEFAULT_LIVE_SECONDS

    def set_seconds(self, seconds: int) -> None:
        if seconds not in LIVE_CHOICES:
            raise ValueError(f"Choose one of {', '.join(map(str, LIVE_CHOICES))} seconds.")
        db.set_setting("live_seconds", str(seconds))
        self._wake.set()  # takes effect now, no restart

    # ---- subscriptions -------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(q)
        self._wake.set()  # back from a pause: refresh now if the data is older than the interval
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _publish(self, snap: dict) -> None:
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()  # keep only the newest snapshot for slow clients
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(snap)

    # ---- refresh loop --------------------------------------------------
    async def run_forever(self) -> None:
        """Live quotes every `seconds()` while a browser has the live stream open. With nobody watching, no
        live quotes: only the hourly background pass (daily history, fund NAVs, benchmarks, news), so the stored
        series get no gaps. A viewer arriving wakes the loop. Clocks are monotonic: a wall-clock change
        (time sync, resume) cannot stall it."""
        while True:
            try:
                self._wake.clear()  # before deciding: a wake that comes after this is never lost
                watched = bool(self._subscribers) or self.snapshot is None  # the startup snapshot is built regardless
                due = (self._last_quotes + self.seconds()) if watched else (self._last_background + BACKGROUND_SECONDS)
                wait = max(due, self._retry_at) - time.monotonic()
                if wait <= 0:
                    try:
                        await self.refresh(quotes=watched)
                    except Exception:  # never let one bad cycle stop the loop
                        log.exception("live refresh failed")
                        # retry after one interval; a separate clock, so the manual Refresh is not throttled by it
                        self._retry_at = time.monotonic() + (self.seconds() if watched else FAIL_BACKOFF_SECONDS)
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), wait)
                except asyncio.TimeoutError:
                    pass
            except Exception:  # deciding failed (a locked database...): the loop must outlive it
                log.exception("live loop error")
                await asyncio.sleep(LOOP_ERROR_PAUSE)

    def request_refresh(self, instrument_id: str) -> None:
        """A holding was added or its symbol changed: fetch its quote and daily history on the loop's next pass,
        not one interval later (up to 15 min), and forget that its history was already done today or failed
        (a fixed typo, another ticker, a re-added ID). Still never two quote passes within 15 s.
        Call from the event loop's thread (async endpoints)."""
        for key in (f"daily:{instrument_id}", f"fund:{instrument_id}"):
            self._daily_done.pop(key, None)
            self._fund_done.pop(key, None)
            self._next_try.pop(key, None)
        self._daily_full.discard(instrument_id)  # another ticker: its whole history, not one month on top of the old one
        now = time.monotonic()
        self._last_quotes = min(self._last_quotes, max(self._last_quotes + MIN_QUOTE_GAP, now) - self.seconds())
        self._last_background = float("-inf")
        self._wake.set()

    def quotes_age(self) -> float:
        """Seconds since live quotes were last fetched (infinite before the first)."""
        return time.monotonic() - self._last_quotes

    async def refresh(self, manual: bool = False, force: bool = False, quotes: bool = True) -> dict | None:
        """force: a setting changed (e.g. reporting currency), so fetch now regardless of the throttle.
        quotes=False: the background pass, history jobs only (nobody is watching live prices)."""
        if manual and not force and self.quotes_age() < MANUAL_MIN_GAP_SECONDS:
            return self.snapshot  # throttled: data is at most 30 s old anyway
        async with self._lock:
            snap = clean(await asyncio.to_thread(self._compute, True, quotes))
            now = time.monotonic()
            self._last_background = now
            if quotes:
                self._last_quotes = now
            self._retry_at = float("-inf")
            self.snapshot = snap
            self._publish(snap)
            return snap

    async def recompute(self) -> dict | None:
        """Rebuild from stored data and the last quotes after an edit, without new network calls."""
        async with self._lock:
            snap = clean(await asyncio.to_thread(self._compute, False))
            self.snapshot = snap
            self._publish(snap)
            return snap

    # ---- work (runs in a worker thread) --------------------------------
    def _compute(self, fetch: bool = True, quotes_too: bool = True) -> dict:
        instruments = db.get_instruments()
        items = instruments.to_dict("records")
        base = db.base_currency()
        symbols = sorted({str(i["market_symbol"]).strip() for i in items if is_yahoo_symbol(i.get("market_symbol"))})
        paid = set(db.get_lots()["paid_currency"].dropna().astype(str).str.upper())
        involved = {str(i["currency"]).strip().upper() for i in items} | paid | {base}
        pivots = sorted(involved - {"USD"})  # every pair is derived through USD
        if fetch:
            self._update_histories(items, pivots)
        live_fetch = fetch and quotes_too
        if live_fetch:
            wanted = symbols + [fx_symbol(c) for c in pivots]
            with ThreadPoolExecutor(max_workers=8) as pool:
                self._quotes = dict(zip(wanted, pool.map(live_quote, wanted)))
            self._quotes_at = datetime.now(timezone.utc)
        quotes = self._quotes
        per_usd: dict[str, float] = {"USD": 1.0}
        for c in pivots:
            q = quotes.get(fx_symbol(c)) or {}
            if q.get("price") and q.get("currency") == c:
                per_usd[c] = q["price"]
                self._errors.pop(f"FX {c}", None)
            elif live_fetch or c not in per_usd:
                self._errors[f"FX {c}"] = f"USD/{c} live rate unavailable: {q.get('error') or 'not fetched yet'}"
        fx_now = ({c: per_usd[base] / per_usd[c] for c in involved - {base} if c in per_usd}
                  if base in per_usd else {})
        snap = build_snapshot({s: quotes[s] for s in symbols if s in quotes}, fx_now, base, dict(self._errors))
        # a background pass fetched no quotes: the snapshot is as old as its quotes, and must not look live
        stamp = (self._quotes_at or datetime.now(timezone.utc)) if fetch and not quotes_too else datetime.now(timezone.utc)
        snap["updated_at"] = stamp.isoformat()
        snap["refresh_seconds"] = self.seconds()
        snap["errors"] = [msg for key, msg in sorted(self._errors.items()) if key.startswith("FX ")]
        return snap

    def _due(self, key: str, done_today: bool) -> bool:
        return not done_today and time.time() >= self._next_try.get(key, 0)

    def _ok(self, key: str, error_key: str) -> None:
        self._next_try.pop(key, None)
        self._errors.pop(error_key, None)

    def _fail(self, key: str, error_key: str, message: str) -> None:
        self._next_try[key] = time.time() + FAIL_BACKOFF_SECONDS
        self._errors[error_key] = message
        log.warning("%s: %s", error_key, message)

    def _update_histories(self, items: list[dict], currencies: list[str]) -> None:
        today = date.today().isoformat()
        jobs = []
        for item in items:
            symbol = str(item.get("market_symbol") or "").strip()
            iid = item["instrument_id"]
            isin = fund_isin(symbol)
            if isin:
                key = f"fund:{iid}"
                if time.time() - self._fund_done.get(key, 0) >= FUND_SECONDS and self._due(key, False):
                    jobs.append(("fund", key, iid, symbol, isin))
            elif is_yahoo_symbol(symbol) and item["asset_type"] != "government_bond":
                key = f"daily:{iid}"
                if self._due(key, self._daily_done.get(key) == today):
                    jobs.append(("daily", key, iid, symbol, None))
        for c in currencies:
            key = f"fxhist:{c}"
            if self._due(key, self._daily_done.get(key) == today):
                jobs.append(("fx", key, c, fx_symbol(c), None))
        for symbol, _label, _kind in research.BENCHMARKS:
            key = f"bench:{symbol}"
            if self._due(key, self._daily_done.get(key) == today):
                jobs.append(("bench", key, symbol, symbol, None))
        if time.time() - self._fund_done.get("news", 0) >= FUND_SECONDS and self._due("news", False):
            stocks = sorted({str(i["market_symbol"]).strip() for i in items
                             if i["asset_type"] == "stock" and is_yahoo_symbol(i.get("market_symbol"))})
            jobs.append(("news", "news", ",".join(stocks), "news", None))
        if not jobs:
            return
        with ThreadPoolExecutor(max_workers=6) as pool:
            for _ in pool.map(self._run_job, jobs):
                pass

    def _run_job(self, job) -> None:
        kind, key, ident, symbol, isin = job
        today = date.today().isoformat()
        try:
            if kind == "fund":
                last = db.latest_price_date(ident)
                start = (pd.Timestamp(last) - pd.Timedelta(days=7)).date().isoformat() if last else "1990-01-01"
                frame, source, error = download_fund_nav(isin, start)
                if error:
                    return self._fail(key, symbol, f"Fund prices: {error}")
                db.save_prices(ident, frame, source)
                self._fund_done[key] = time.time()
            elif kind == "daily":
                # in full once per run (repairs a history stored before a split or dividend re-adjusted it) and
                # after a pause longer than a month can bridge, else one month at a time, again in full whenever
                # Yahoo re-adjusted the past closes
                last = newest_yahoo(ident)  # imported closes don't bridge a gap in Yahoo's
                full = (ident not in self._daily_full or not last
                        or pd.Timestamp(last) < pd.Timestamp.today().normalize() - MONTH_COVERS)
                period, oldest = full_period(ident)
                frame, error = download_daily_history(symbol, period=period if full else "1mo")
                if not error and not full and readjusted(ident, frame):
                    full = True
                    frame, error = download_daily_history(symbol, period=period)
                if error:
                    return self._fail(key, symbol, f"Daily history: {error}")
                if full and oldest:
                    frame = frame[[pd.Timestamp(d).date().isoformat() >= oldest for d in frame["date"]]]
                # a full download replaces every Yahoo close in its range (another listing's, an older
                # adjustment's); imported ones stay
                db.save_prices(ident, without_imported(ident, frame), YAHOO, replace_except=IMPORTED if full else None)
                self._daily_done[key] = today
                if full:
                    self._daily_full.add(ident)
            elif kind == "bench":
                # 5 years on the first fetch, and once per run when an older install stored only 2 years; a
                # benchmark Yahoo has no older data for is then not downloaded in full every day
                stored = research._series(ident)
                cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=BENCH_BACKFILL_DAYS)
                full = (stored.empty or (ident not in self._bench_backfilled and stored.index.min() > cutoff)
                        or stored.index.max() < pd.Timestamp.today().normalize() - MONTH_COVERS)  # a long pause
                frame, error = download_daily_history(ident, period="5y" if full else "1mo")
                if error:
                    return self._fail(key, f"benchmark {ident}", f"Benchmark {ident}: {error}")
                research.save_benchmark(ident, frame)
                if full:
                    self._bench_backfilled.add(ident)
                self._daily_done[key] = today
                return self._ok(key, f"benchmark {ident}")
            elif kind == "news":
                failed = []
                for label, url in research.DEFAULT_FEEDS:
                    try:
                        research.save_news(label, research.fetch_feed(url))
                    except Exception as exc:  # one broken feed must not hide the others
                        failed.append(f"{label}: {exc}")
                for sym in [s for s in ident.split(",") if s]:
                    try:
                        research.save_news("Yahoo Finance (aggregated)", research.parse_yahoo_news(yf.Ticker(sym).news, sym))
                    except Exception as exc:
                        failed.append(f"{sym} news: {exc}")
                self._fund_done["news"] = time.time()
                if failed:
                    log.warning("news: %s", "; ".join(failed))
                return self._ok(key, "news")
            else:
                rates, error = download_fx_history(ident)
                if error:
                    return self._fail(key, f"FX history {ident}", error)
                db.save_fx_usd(ident, rates, "Yahoo Finance via yfinance")
                self._daily_done[key] = today
            self._ok(key, symbol if kind != "fx" else f"FX history {ident}")
        except Exception as exc:  # one source must not break the others
            self._fail(key, symbol if kind != "fx" else f"FX history {ident}", f"{kind} update failed: {exc}")


hub = LiveHub()
