"""Checking a market symbol before a holding is created from it: the add-holding wizard (GET /api/quote) and the
AI analyst's "add a holding" cards use the same check, so both store the same currency and price divisor."""
from __future__ import annotations

import pandas as pd

from analyzer.engine import MINOR_UNITS
from analyzer import market
from analyzer.portfolio import is_yahoo_symbol

OFFLINE = "The price source couldn't be reached, so the symbol couldn't be checked."


class SymbolError(ValueError):
    """`status`: 400 for a symbol that can't be one, 404 for one with no price, 503 when the price source couldn't
    be asked (network trouble: the symbol may be fine)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def quote_out(price: float, raw_ccy: str | None, as_of: str | None, source: str) -> dict:
    """{price (as quoted), currency (major), quote_currency (as quoted), divisor, as_of, source}."""
    raw = (raw_ccy or "").strip()
    major, divisor = MINOR_UNITS.get(raw, (raw.upper() or None, 1.0))
    return {"price": price, "currency": major, "quote_currency": raw or None, "divisor": divisor,
            "as_of": as_of, "source": source}


def quote_symbol(symbol: str) -> dict:
    """The latest price of a Yahoo ticker or a fund:<ISIN> (network). SymbolError when there is none.

    yfinance and the fund feeds report an unreachable source as a missing price (yfinance hides its own network
    errors), so a missing price is only "no price" (404) once the source is known to have answered; otherwise 503."""
    symbol = (symbol or "").strip()
    isin = market.fund_isin(symbol)
    if isin:
        start = (pd.Timestamp.today() - pd.Timedelta(days=21)).date().isoformat()
        frame, source, error = market.download_fund_nav(isin, start)
        if error or frame.empty:
            # "no price" only when every source answered without data (market.download_fund_nav's own "<source>: no
            # data..." entries); an exception from either one (timeout, HTTP 503) means it couldn't be asked
            parts = (error or "").split("; ")
            if not all(p.split(": ", 1)[-1].startswith("no data") for p in parts):
                raise SymbolError(OFFLINE, 503)
            raise SymbolError(error or f"No recent price for {isin}.", 404)
        last = frame.sort_values("date").iloc[-1]
        return quote_out(float(last["close"]), str(last["currency"]), pd.Timestamp(last["date"]).date().isoformat(), source)
    if not is_yahoo_symbol(symbol):
        raise SymbolError("Use a Yahoo ticker such as VWCE.DE, or fund:<ISIN> for a Hungarian fund.", 400)
    q = market.live_quote(symbol)  # through the module: tests patch analyzer.market
    if not q.get("price"):
        # "Quote failed": live_quote caught an exception (rate limit, timeout) rather than an answer without a price
        # otherwise ask the endpoint itself (not the site's front page): it answers 404 for a symbol it doesn't know,
        # 429 or 5xx when it is in trouble, which yfinance would have reported as a missing price too
        if str(q.get("error") or "").startswith("Quote failed") or not market.reachable(market.yahoo_chart_url(symbol)):
            raise SymbolError(OFFLINE, 503)
        raise SymbolError(q.get("error") or f"No price for {symbol}.", 404)
    when = q.get("time")
    return quote_out(float(q["price"]), q.get("currency"), pd.Timestamp(when).isoformat() if when is not None else None,
                     "Yahoo Finance")
