"""Portfolio snapshot and value history, built from stored data plus live quotes.

No network access here: callers pass in live quotes and FX rates. Everything unknown stays None
and is explained in the row's `check`; totals only sum what is known and report what they exclude.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from analyzer import db
from analyzer.engine import currency_mismatch, latest_price_currency, summarize_position
from analyzer.market import fund_isin

FX_MAX_AGE_DAYS = 7  # a buy-date FX rate may come from the nearest earlier trading day, at most this far back
SPARK_POINTS = 30


def is_yahoo_symbol(symbol: Any) -> bool:
    """Yahoo tickers never contain ':'; other sources use a 'source:ID' symbol."""
    return isinstance(symbol, str) and bool(symbol.strip()) and ":" not in symbol


class RateBook:
    """Historical FX from stored daily units-per-USD: rate(ccy, day) = base per 1 unit of ccy on that day.

    Uses the nearest earlier trading day within FX_MAX_AGE_DAYS; memoised for one snapshot/history build.
    """

    def __init__(self, base: str):
        self.base = base.upper()
        self._cache: dict[tuple[str, str], float | None] = {}

    def _per_usd(self, ccy: str, day: str) -> float | None:
        key = (ccy, day)
        if key not in self._cache:
            found = db.fx_usd_on_or_before(ccy, day)
            ok = found and pd.Timestamp(found[1]) >= pd.Timestamp(day) - pd.Timedelta(days=FX_MAX_AGE_DAYS)
            self._cache[key] = found[0] if ok else None
        return self._cache[key]

    def rate(self, ccy: str, day: str) -> float | None:
        ccy = ccy.upper()
        if ccy == self.base:
            return 1.0
        base_per_usd, ccy_per_usd = self._per_usd(self.base, day), self._per_usd(ccy, day)
        return base_per_usd / ccy_per_usd if base_per_usd and ccy_per_usd else None


def _num(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def build_snapshot(quotes: dict[str, dict], fx_now: dict[str, float], base: str = "HUF",
                   errors: dict[str, str] | None = None) -> dict:
    """Current value of every holding in the base currency. quotes: Yahoo symbol -> live_quote() result.

    fx_now: base-currency value of one unit of each currency (from live USD-pivot quotes).
    """
    errors = errors or {}
    base = base.upper()
    book = RateBook(base)
    instruments = db.get_instruments()
    lots = db.get_lots()
    rows = []
    for item in instruments.to_dict("records"):
        currency = str(item["currency"]).strip().upper()
        item_lots = lots[lots["instrument_id"] == item["instrument_id"]].to_dict("records")
        history = db.get_recent_prices(item["instrument_id"], SPARK_POINTS + 2)  # only the tail is needed
        symbol = str(item.get("market_symbol") or "").strip()
        quote = quotes.get(symbol) if is_yahoo_symbol(symbol) else None
        notes = []
        if quote and quote.get("price") is not None:
            price, price_currency, source = quote["price"], quote["currency"], "live"
            as_of = quote["time"].isoformat() if quote.get("time") is not None else None
            prev = quote.get("prev_close")
        else:
            price = float(history.iloc[-1]["close"]) if not history.empty else None
            price_currency = latest_price_currency(history)
            as_of = history.iloc[-1]["date"].date().isoformat() if not history.empty else None
            prev = float(history.iloc[-2]["close"]) if len(history) > 1 else None
            source = "daily" if fund_isin(symbol) else "stored"
            if quote and quote.get("error"):
                notes.append(f"Live quote failed ({quote['error']}); showing last stored price")
        if symbol in errors:
            notes.append(errors[symbol])

        pos = summarize_position(item, item_lots, price, price_currency, fx_now, book.rate, base)
        today_base = today_pct = None
        if (not pos["closed"] and pos["value_base"] is not None and prev and price
                and not currency_mismatch(currency, price_currency, item.get("price_divisor"))):
            today_pct = price / prev - 1
            today_base = pos["value_base"] - pos["value_base"] / (1 + today_pct)
        closes = [float(c) for c in history["close"].tail(SPARK_POINTS)] if not history.empty else []
        if source == "live" and price is not None:
            closes = (closes + [price])[-SPARK_POINTS:]
        check = "; ".join([c for c in [pos["check"], *notes] if c])
        rows.append({
            "id": item["instrument_id"], "name": item["name"], "type": item["asset_type"], "currency": currency,
            "symbol": symbol or None, "price": price, "price_currency": price_currency, "price_source": source,
            "as_of": as_of, "prev_close": prev, "quantity": pos["quantity"], "avg_cost": pos["avg_cost"],
            "value": pos["value"], "value_base": pos["value_base"], "cost_base": pos["cost_base"],
            "pnl": pos["pnl"], "pnl_base": pos["pnl_base"], "pnl_pct": pos["pnl_pct"],
            "today_base": today_base, "today_pct": today_pct, "lots": len(item_lots), "sells": pos["sells"],
            "realized_base": pos["realized_base"], "closed": pos["closed"], "spark": closes, "check": check,
        })

    # A closed holding (everything sold) is worth 0 and is no part of the allocation or the counts below;
    # only its realized P/L still counts.
    held = [r for r in rows if not r["closed"]]
    valued = [r for r in held if r["value_base"] is not None]
    total = sum(r["value_base"] for r in valued)
    for r in rows:
        r["weight"] = (r["value_base"] / total
                       if not r["closed"] and r["value_base"] is not None and total else None)
    with_pnl = [r for r in valued if r["pnl_base"] is not None]
    cost = sum(r["cost_base"] for r in with_pnl)
    with_today = [r for r in valued if r["today_base"] is not None]
    today = sum(r["today_base"] for r in with_today)
    yesterday = sum(r["value_base"] - r["today_base"] for r in with_today)
    return {
        "base_currency": base,
        "rows": rows,
        "totals": {
            "value_base": total if valued else None,
            "pnl_base": sum(r["pnl_base"] for r in with_pnl) if with_pnl else None,
            "pnl_pct": (sum(r["pnl_base"] for r in with_pnl) / cost) if cost else None,
            "today_base": today if with_today else None,
            "today_pct": (today / yesterday) if yesterday else None,
            # unknown (None) as soon as one holding's is: a partial sum would pass for the whole
            "realized_base": (None if any(r["realized_base"] is None for r in rows)
                              else sum(r["realized_base"] for r in rows)),
            "holdings": len(held), "closed": len(rows) - len(held), "valued": len(valued),
            "with_pnl": len(with_pnl), "with_today": len(with_today),
        },
        "fx": fx_now,
    }


def _today() -> pd.Timestamp:
    """Midnight today; a function so tests can pin the date."""
    return pd.Timestamp.today().normalize()


def value_history(days: int | None, base: str = "HUF") -> dict:
    """Daily portfolio value in the base currency, rebuilt from stored closes x quantity held x stored FX.

    Quantity on a date comes from the transactions dated on or before it (buys add, sales subtract); holdings without transactions use
    their current quantity throughout (stated in the UI). A holding counts from its first stored
    price on (its last earlier price is carried into short windows), so the curve starts when data starts.
    """
    instruments = db.get_instruments()
    if instruments.empty:
        return {"currency": base.upper(), "points": [], "included": []}
    lots = db.get_lots()
    end = _today()
    start = end - pd.Timedelta(days=days) if days else None
    # get_all_prices adds each holding's last close before `since`, so the first day can carry it forward
    since = start.date().isoformat() if start is not None else None
    series = []
    base = base.upper()
    fx_cache: dict[str, pd.Series] = {}

    def per_usd(ccy: str) -> pd.Series | None:
        if ccy == "USD":
            return None  # 1 on every date
        if ccy not in fx_cache:
            fx_cache[ccy] = db.fx_usd_series(ccy)
        return fx_cache[ccy]
    all_prices = db.get_all_prices(since)  # one query for every holding
    # the price currency over the WHOLE history (as the live snapshot uses), not just this window's rows
    price_ccy = db.latest_price_currencies()
    by_id = {iid: g for iid, g in all_prices.groupby("instrument_id", sort=False)} if not all_prices.empty else {}
    for item in instruments.to_dict("records"):
        currency = str(item["currency"]).strip().upper()
        prices = by_id.get(item["instrument_id"])
        if prices is None or prices.empty or currency_mismatch(currency, price_ccy.get(item["instrument_id"]), item.get("price_divisor")):
            continue
        close = prices.set_index("date")["close"].astype(float)
        close = close[~close.index.duplicated(keep="last")]
        close = close[close.index <= end]  # a bar dated after today (time zones, a CSV typo) is not shown yet
        if close.empty:
            continue
        # Every holding gets a point on the window's first day and today, so a rarely priced holding
        # (bond, hand-imported fund) is carried to the right edge and a short window is never empty.
        idx = close.index.union(pd.DatetimeIndex([end] if start is None else [start, end]))
        item_lots = lots[lots["instrument_id"] == item["instrument_id"]]
        if not item_lots.empty:
            # sales count negative; never below zero (a sale larger than the units held then, e.g. an old import)
            signed = item_lots["quantity"].where(item_lots["side"] != "sell", -item_lots["quantity"])
            held = item_lots.assign(d=pd.to_datetime(item_lots["buy_date"]), signed=signed).groupby("d")["signed"].sum().cumsum().clip(lower=0.0)
            idx = idx.union(held.index[held.index <= end])  # a transaction after the last price counts from its date
            qty = held.reindex(idx).ffill().fillna(0.0)
        else:
            qty = pd.Series(float(item.get("quantity") or 0.0), index=idx)
        close = close.reindex(idx).ffill()  # last known close carried over non-trading days, up to today
        value = qty * close / float(item.get("price_divisor") or 1.0)
        if currency != base:
            # base per unit = base_per_usd / ccy_per_usd, each carried forward over non-trading days
            factor = pd.Series(1.0, index=value.index)
            for ccy, power in ((base, 1), (currency, -1)):
                fx = per_usd(ccy)
                if fx is None:
                    continue
                if fx.empty:
                    factor = None
                    break
                factor = factor * fx.reindex(value.index.union(fx.index)).ffill().reindex(value.index) ** power
            if factor is None:
                continue
            value = value * factor
        series.append(value.rename(item["instrument_id"]))
    if not series:
        return {"currency": base.upper(), "points": [], "included": []}
    frame = pd.concat(series, axis=1).sort_index()
    frame = frame.ffill()  # carry each holding's last price over its non-trading days
    total = frame.sum(axis=1, min_count=1).dropna()
    if start is not None:
        total = total[total.index >= start]
    return {
        "currency": base,
        "points": [{"date": d.date().isoformat(), "value": round(float(v), 2)} for d, v in total.items()],
        "included": [str(s.name) for s in series],
    }
