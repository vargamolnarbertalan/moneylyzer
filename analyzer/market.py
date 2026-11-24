from __future__ import annotations

import csv
import json
import numbers
import re
import time
import urllib.parse
import urllib.request

import pandas as pd
import yfinance as yf

# Hungarian investment funds: market_symbol "fund:<ISIN>". Official sources, plain HTTP, no key.
# Both are the sites' own (undocumented) data endpoints, so they may change without notice.
FUND_PREFIX = "fund:"
OTP_NAV_URL = "https://www.otpalapkezelo.hu/apps3/fund-assets/api/yield-graph/data/{isin}"
BAMOSZ_NAV_URL = "https://www.bamosz.hu/bamosz-public-alapoldal-portlet/kuka.download"
HTTP_TIMEOUT_SECONDS = 8  # short: a silently dropped connection must not stall the live refresh
ISIN_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")  # always used with fullmatch
EMPTY_PRICES = ["date", "close", "currency"]


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Turn a yfinance history frame into date/close rows keyed by the exchange's own trading date."""
    frame = frame.reset_index()
    date_col = "Date" if "Date" in frame.columns else "Datetime"
    frame = frame[[date_col, "Close"]].rename(columns={date_col: "date", "Close": "close"})
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.dt.tz is not None:
        # Drop the timezone without converting: midnight Europe/Berlin must stay that calendar day,
        # converting to UTC first would shift European and Asian closes one day back.
        dates = dates.dt.tz_localize(None)
    frame["date"] = dates
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.dropna(subset=["date", "close"])


def download_daily_history(symbol: str, period: str = "5y") -> tuple[pd.DataFrame, str | None]:
    """Download daily adjusted-close history from Yahoo Finance via yfinance.

    This sends the requested public ticker symbol to Yahoo Finance. It does not
    send portfolio quantities, cost basis, bank information or the local DB.
    """
    ticker = (symbol or "").strip()
    if not ticker:
        return pd.DataFrame(columns=["date", "close", "currency"]), "No market symbol supplied."
    try:
        tkr = yf.Ticker(ticker)
        frame = tkr.history(
            period=period,
            interval="1d",
            auto_adjust=True,
            actions=False,
        )
        if frame is None or frame.empty or "Close" not in frame.columns:
            return pd.DataFrame(columns=["date", "close", "currency"]), "No price history returned for this symbol."
        frame = normalize_history(frame)
        # Currency comes from the same response (no second request). Without it the holding
        # could be valued in the wrong unit (e.g. GBp pence as GBP), so nothing is saved.
        currency = (tkr.history_metadata or {}).get("currency")
        if not currency:
            return pd.DataFrame(columns=["date", "close", "currency"]), "Yahoo did not report the quote currency; nothing saved. Try again later."
        frame["currency"] = currency
        return frame, None
    except Exception as exc:  # yfinance errors vary by upstream/network conditions
        return pd.DataFrame(columns=["date", "close", "currency"]), f"Download failed: {exc}"


def quote_time(stamp) -> pd.Timestamp | None:
    """UTC timestamp from Yahoo's regularMarketTime: yfinance gives a tz-aware Timestamp, the raw API epoch seconds."""
    if stamp is None or (not isinstance(stamp, str) and pd.isna(stamp)):
        return None
    if isinstance(stamp, numbers.Real) and not isinstance(stamp, bool):
        ts = pd.Timestamp(int(stamp), unit="s")  # numpy ints too; never read as nanoseconds
    else:
        ts = pd.Timestamp(stamp)
    if pd.isna(ts):
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def previous_close(frame, quote_ts) -> float | None:
    """Close of the last daily bar before the quote's own trading day (exchange-local dates)."""
    if frame is None or getattr(frame, "empty", True) or "Close" not in frame.columns or quote_ts is None:
        return None
    bars = normalize_history(frame)
    tz = frame.index.tz
    local = quote_ts.tz_convert(tz) if tz is not None else quote_ts  # quote_ts is always tz-aware (UTC)
    quote_day = local.tz_localize(None).normalize()
    earlier = bars[bars["date"].dt.normalize() < quote_day]
    return float(earlier["close"].iloc[-1]) if not earlier.empty else None


def live_quote(symbol: str) -> dict:
    """Latest Yahoo quote for one symbol: {"price", "currency", "time", "error"}.

    Uses one small 5-day request; Yahoo's response metadata carries the current market price
    (regularMarketPrice), its timestamp and currency. fast_info.last_price is avoided because it
    downloads a full year of history on every call. Quotes can be delayed (~15 min on some exchanges).
    """
    out = {"price": None, "currency": None, "time": None, "prev_close": None, "error": None}
    try:
        tkr = yf.Ticker(symbol)
        frame = tkr.history(period="5d", interval="1d", auto_adjust=False, actions=False)
        md = tkr.history_metadata or {}
        price, stamp = md.get("regularMarketPrice"), md.get("regularMarketTime")
        if price is None or not float(price) > 0:
            out["error"] = "No live price returned."
            return out
        if not md.get("currency"):
            # Without a currency the price could be in pence (GBp) or another unit: never value it.
            out["error"] = "Yahoo did not report the quote currency."
            return out
        out["price"] = float(price)
        out["currency"] = md.get("currency")
        out["time"] = quote_time(stamp)
        out["prev_close"] = previous_close(frame, out["time"])
    except Exception as exc:  # network/upstream errors vary
        out["error"] = f"Quote failed: {exc}"
    return out


CURRENCY_RE = re.compile(r"[A-Z]{3}")


def fx_symbol(currency: str) -> str:
    """Yahoo symbol for units of `currency` per 1 USD, e.g. EUR -> USDEUR=X (verified for 16 major and
    emerging-market currencies on 2026-11-24). Every pair is derived through USD."""
    return f"USD{currency.upper()}=X"


FX_HISTORY_START = "2010-01-01"


def download_fx_history(currency: str) -> tuple[pd.DataFrame, str | None]:
    """Daily closes of units-per-USD for currency, from a fixed start through yesterday, from Yahoo.

    The window never depends on the user's data (no buy dates leave the PC), and today's
    still-moving bar is dropped so it is never stored as a close.
    """
    try:
        frame = yf.Ticker(fx_symbol(currency)).history(
            start=FX_HISTORY_START, interval="1d", auto_adjust=False, actions=False,
        )
        if frame is None or frame.empty or "Close" not in frame.columns:
            return pd.DataFrame(columns=["date", "per_usd"]), f"No USD/{currency} history returned."
        rates = normalize_history(frame).rename(columns={"close": "per_usd"})
        return rates[rates["date"] < pd.Timestamp.today().normalize()], None
    except Exception as exc:
        return pd.DataFrame(columns=["date", "per_usd"]), f"USD/{currency} download failed: {exc}"


# Yahoo quote types the app can hold, mapped to its asset types (anything else is not offered).
SEARCH_TYPES = {"EQUITY": "stock", "ETF": "etf", "MUTUALFUND": "mutual_fund", "FUTURE": "commodity"}
_search_cache: dict[str, tuple[float, list[dict]]] = {}
SEARCH_TTL = 600.0


def _yahoo_search(query: str, limit: int) -> list:
    """Yahoo's search endpoint through yfinance's session (cookies/crumb), WITHOUT its cache.

    yf.Search goes through YfData.cache_get, a process-wide lru_cache of raw responses, and turns an
    error page into "no results" (hide_exceptions), which then sticks until restart. YfData and
    _BASE_URL_ are yfinance internals: re-check them when bumping the pinned yfinance version.
    """
    from yfinance.const import _BASE_URL_
    from yfinance.data import YfData

    params = {"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0, "enableFuzzyQuery": False,
              "quotesQueryId": "tss_match_phrase_query", "enableCb": False, "enableNavLinks": False,
              "enableResearchReports": False, "enableCulturalAssets": False, "recommendedCount": 0}
    resp = YfData().get(url=f"{_BASE_URL_}/v1/finance/search", params=params, timeout=10)
    if resp is None or getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"HTTP {getattr(resp, 'status_code', '?')}")
    data = resp.json()
    if not isinstance(data, dict) or not isinstance(data.get("quotes"), list):
        raise RuntimeError("unexpected response")
    return data["quotes"]


def search_instruments(query: str, limit: int = 8) -> list[dict]:
    """Candidates for the add-holding wizard: [{symbol, name, type, exchange}], best match first.

    A Hungarian ISIN is offered as an OTP/BAMOSZ fund ("fund:<ISIN>") as well. Sends only the
    typed text to Yahoo; successful results are cached for 10 minutes, failures never. Raises
    RuntimeError if Yahoo fails and there is nothing else to offer.
    """
    q = " ".join(str(query).split())[:60]
    key = q.lower()
    now = time.time()
    hit = _search_cache.get(key)
    if hit and now - hit[0] < SEARCH_TTL:
        return hit[1]
    out: list[dict] = []
    upper = q.upper()
    if ISIN_RE.fullmatch(upper) and upper.startswith("HU"):
        out.append({"symbol": f"fund:{upper}", "name": f"Hungarian investment fund {upper}", "type": "mutual_fund",
                    "exchange": "OTP Alapkezelő / BAMOSZ"})
    try:
        found = _yahoo_search(q, limit)
    except Exception as exc:  # network, rate limit, error page, changed response: not cached
        if out:
            return out
        raise RuntimeError(f"Search is unavailable right now ({type(exc).__name__}).") from None
    for item in found:
        if not isinstance(item, dict):
            continue
        kind = SEARCH_TYPES.get(str(item.get("quoteType") or "").upper())
        symbol = str(item.get("symbol") or "").strip()
        if not kind or not symbol or ":" in symbol:
            continue
        name = str(item.get("longname") or item.get("shortname") or "")
        if kind == "commodity" and (not symbol.endswith("=F") or "single stock" in name.lower() or "stock fut" in name.lower()):
            continue  # only Yahoo's continuous commodity futures (GC=F); not single-stock or dated contracts
        out.append({"symbol": symbol, "name": str(item.get("longname") or item.get("shortname") or symbol)[:200],
                    "type": kind, "exchange": str(item.get("exchDisp") or item.get("exchange") or "")[:40]})
    out = out[:limit]
    if len(_search_cache) > 200:
        _search_cache.clear()
    _search_cache[key] = (now, out)
    return out


def fund_isin(symbol) -> str | None:
    """ISIN from a 'fund:<ISIN>' market symbol, or None."""
    if not isinstance(symbol, str) or not symbol.lower().startswith(FUND_PREFIX):
        return None
    isin = symbol[len(FUND_PREFIX):].strip().upper()
    return isin if ISIN_RE.fullmatch(isin) else None


def _http(url: str, data: dict | None = None) -> bytes:
    body = urllib.parse.urlencode(data).encode("ascii") if data else None
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "moneylyzer"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def parse_otp_nav(payload: bytes) -> pd.DataFrame:
    """OTP Alapkezelő yield-graph JSON: {"currency": "HUF", "values": [{"date": "YYYY-MM-DD", "value": 3.28}]}."""
    data = json.loads(payload)
    values = data.get("values") or []
    frame = pd.DataFrame(values, columns=["date", "value"]).rename(columns={"value": "close"})
    frame["date"] = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["currency"] = data.get("currency")
    return frame.dropna(subset=["date", "close"]).sort_values("date")[EMPTY_PRICES]


def parse_bamosz_nav(payload: bytes) -> pd.DataFrame:
    """BAMOSZ kuka.download CSV (ISO-8859-2, ';', decimal comma, newest first, metadata lines on top)."""
    lines = payload.decode("iso-8859-2").splitlines()
    currency = None
    for line in lines:
        if line.startswith('"Befektetési jegy devizaneme:"'):
            currency = next(csv.reader([line], delimiter=";"))[1].strip() or None
    start = next(i for i, line in enumerate(lines) if line.startswith('"Dátum"'))
    reader = csv.reader(lines[start:], delimiter=";")
    header = next(reader)
    i_date, i_price = header.index("Dátum"), header.index("Árfolyam")
    rows = [(r[i_date], r[i_price].replace(",", ".")) for r in reader if len(r) > i_price and r[i_price]]
    frame = pd.DataFrame(rows, columns=["date", "close"])
    frame["date"] = pd.to_datetime(frame["date"], format="%Y/%m/%d", errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["currency"] = currency
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")
    # BAMOSZ sometimes repeats Friday's price on weekend dates. Drop a weekend row only when it
    # copies the previous price, so a real Saturday working-day valuation is kept.
    weekend = frame["date"].dt.dayofweek >= 5
    repeats = frame["close"].eq(frame["close"].shift(1))
    return frame[~(weekend & repeats)][EMPTY_PRICES]


def download_fund_nav(isin: str, start: str) -> tuple[pd.DataFrame, str, str | None]:
    """Daily price per unit of a Hungarian fund from start (ISO date) to today.

    Returns (frame[date, close, currency], source label, error). Tries the OTP Alapkezelő endpoint,
    then BAMOSZ. Sends only the public ISIN.
    """
    if not ISIN_RE.fullmatch(isin or ""):
        return pd.DataFrame(columns=EMPTY_PRICES), "", f"Not a valid ISIN: {isin!r}"
    errors = []
    try:
        query = urllib.parse.urlencode({
            "type": "ASSET_PRICE", "start-date": start, "end-date": pd.Timestamp.today().date().isoformat(),
        })
        frame = parse_otp_nav(_http(OTP_NAV_URL.format(isin=isin) + "?" + query))
        if not frame.empty and frame["currency"].notna().all():
            return frame, "OTP Alapkezelő", None
        errors.append("OTP Alapkezelő: no data")
    except Exception as exc:
        errors.append(f"OTP Alapkezelő: {exc}")
    try:
        frame = parse_bamosz_nav(_http(BAMOSZ_NAV_URL, {"isin": isin, "separator": "pontosvesszo"}))
        frame = frame[frame["date"] >= pd.Timestamp(start)]
        if not frame.empty and frame["currency"].notna().all():
            return frame, "BAMOSZ", None
        errors.append("BAMOSZ: no data or no currency")
    except Exception as exc:
        errors.append(f"BAMOSZ: {exc}")
    return pd.DataFrame(columns=EMPTY_PRICES), "", "; ".join(errors)
