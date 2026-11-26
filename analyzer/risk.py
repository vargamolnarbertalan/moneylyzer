"""Portfolio risk from stored data only (no AI, no network): concentration and exposure from today's weights,
and how today's mix of holdings would have moved over the past year or three years.

The history replays the current weights on each holding's stored daily closes, converted into the reporting
currency at each day's rate (rebalanced daily to those weights). So buys and sales don't distort it the way
the value-over-time curve's jumps would: it answers "how risky is what I hold now", not "how did my account
do". Past prices only; not a forecast.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analyzer import db, research
from analyzer.engine import currency_mismatch

MIN_OBSERVATIONS = 60  # a holding needs about three months of daily prices in the window to count
COVERAGE_GAP = pd.Timedelta(days=10)  # its prices must start and end within this of the window's edges
MAX_PAUSE = pd.Timedelta(days=14)  # nor pause longer inside it: the longest exchange holidays (Lunar New Year,
#                                    Golden Week) fit, a missing month does not (it would read as one day's move)
MAX_RATE_AGE = pd.Timedelta(days=7)  # an exchange rate older than this is no rate (as RateBook for today's values)
BENCHMARK = ("IWDA.AS", "MSCI World (IWDA)", "EUR")


def _held(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("closed") and r.get("weight") is not None and r["weight"] > 0]


def unvalued(rows: list[dict]) -> list[str]:
    """Holdings still held but without a value right now (no price or exchange rate): they can't be weighed."""
    return [r["name"] for r in rows if not r.get("closed") and r.get("weight") is None]


def concentration(rows: list[dict]) -> dict | None:
    """Largest holding, the top three together, and the effective number of holdings (1 / sum of squared
    weights: 10 equal holdings give 10, one dominant holding gives close to 1)."""
    held = sorted(_held(rows), key=lambda r: r["weight"], reverse=True)
    if not held:
        return None
    largest = held[0]
    return {
        "largest": {"id": largest["id"], "name": largest["name"], "weight": largest["weight"]},
        "top3": sum(r["weight"] for r in held[:3]),
        "effective": 1.0 / sum(r["weight"] ** 2 for r in held),
        "holdings": len(held),
    }


def exposure(rows: list[dict]) -> dict:
    """Share of the value per currency (the holding's own currency) and per asset type."""
    out = {}
    for field in ("currency", "type"):
        totals: dict[str, float] = {}
        for r in _held(rows):
            totals[r[field]] = totals.get(r[field], 0.0) + r["weight"]
        out[field] = [{"key": k, "weight": v} for k, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)]
    return out


def _carry(s: pd.Series, index: pd.DatetimeIndex, max_age: pd.Timedelta) -> pd.Series:
    """`s` on each date of `index`: its latest value on or before that date, NaN when that is older than max_age."""
    s = s[~s.index.duplicated(keep="last")].sort_index()
    union = s.index.union(index)
    values = s.reindex(union).ffill()
    seen = pd.Series(s.index, index=s.index).reindex(union).ffill()
    values[(union.to_series() - seen) > max_age] = np.nan
    return values.reindex(index)


def _fx_factor(ccy: str, base: str, index: pd.DatetimeIndex, cache: dict) -> pd.Series | None:
    """Base-currency value of one unit of `ccy` on each date (stored daily USD pivots, at most a week old)."""
    if ccy == base:
        return pd.Series(1.0, index=index)
    factor = pd.Series(1.0, index=index)
    for c, power in ((base, 1), (ccy, -1)):
        if c == "USD":
            continue
        if c not in cache:
            fx = db.fx_usd_series(c)
            if c == "EUR":  # EUR history stops when no holding or base needs EUR: the stored EUR/USD benchmark fills in
                eurusd = research._series("EURUSD=X")
                if not eurusd.empty:
                    fx = fx.combine_first(1.0 / eurusd[eurusd > 0]) if not fx.empty else 1.0 / eurusd[eurusd > 0]
            cache[c] = fx
        fx = cache[c]
        if fx.empty:
            return None
        factor = factor * _carry(fx, index, MAX_RATE_AGE) ** power
    return None if factor.isna().all() else factor


def _coverage(index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp, one: str, many: str) -> str | None:
    """Why dates inside the window don't cover all of it, naming what is missing ("price"/"prices", or an
    exchange rate, so the reason points at what to fix); None when they do."""
    if index.empty:
        return f"no {many} in this range"
    if index[-1] < end - COVERAGE_GAP:
        return f"no {one} since {index[-1].date().isoformat()}"
    if index[0] > start + COVERAGE_GAP:
        return f"{many} only from {index[0].date().isoformat()}"
    gaps = index.to_series().diff()
    if gaps.max() > MAX_PAUSE:
        after = gaps.idxmax()
        before = index[index.get_loc(after) - 1]
        return f"no {many} from {before.date().isoformat()} to {after.date().isoformat()}"
    return None


def _drawdown(wealth: pd.Series) -> pd.Series:
    return wealth / wealth.cummax() - 1


def history(rows: list[dict], base: str, days: int, today: pd.Timestamp | None = None) -> dict:
    """Volatility, max drawdown, worst day and month, beta and each holding's share of the risk, for today's
    mix over the last `days` days. {"error": ...} when there is not enough history to say anything."""
    base = base.upper()
    end = (today or pd.Timestamp.today()).normalize()
    start = end - pd.Timedelta(days=days)
    held = _held(rows)
    if not held:
        return {"error": "No holding has a value yet."}
    items = {i["instrument_id"]: i for i in db.get_instruments().to_dict("records")}
    price_ccy = db.latest_price_currencies()
    prices = db.get_all_prices((start - pd.Timedelta(days=10)).date().isoformat())
    by_id = {iid: g.set_index("date")["close"].astype(float) for iid, g in prices.groupby("instrument_id", sort=False)} if not prices.empty else {}
    fx_cache: dict[str, pd.Series] = {}

    # Each holding counted must have prices across the whole window: a holding listed (or last priced) part of
    # the way through would count as "no move" for the rest and hide risk, and a month without prices would
    # put that month's whole move on one day. It is named instead, with why.
    series, weights = {}, {}
    excluded = [{"name": n, "reason": "no value right now (no price or exchange rate)"} for n in unvalued(rows)]
    for r in held:
        item = items.get(r["id"])
        close = by_id.get(r["id"])
        ccy = str((item or {}).get("currency") or "").strip().upper()
        reason = None
        if item is None or close is None or close.empty:
            reason = "no stored prices"
        elif currency_mismatch(ccy, price_ccy.get(r["id"]), item.get("price_divisor")):
            reason = "its prices are in another currency than the holding"
        else:
            close = close[~close.index.duplicated(keep="last")].sort_index()
            factor = _fx_factor(ccy, base, close.index, fx_cache) if ccy else None
            if factor is None:
                reason = f"no {ccy}/{base} exchange rate in this range" if ccy else "no currency set"
            else:
                own = close[(close.index >= start) & (close.index <= end)]
                in_window = (close * factor).dropna()
                in_window = in_window[(in_window.index >= start) & (in_window.index <= end)]
                rate = f"{ccy}/{base} rate"
                reason = (_coverage(own.index, start, end, "price", "prices")
                          or (f"only {len(own)} prices in this range (needs {MIN_OBSERVATIONS})"
                              if len(own) < MIN_OBSERVATIONS else None)
                          or _coverage(in_window.index, start, end, rate, rate))  # the prices are there: the rate isn't
                if reason is None:
                    series[r["id"]] = in_window
                    weights[r["id"]] = r["weight"]
        if reason:
            excluded.append({"name": r["name"], "reason": reason})
    if not series:
        return {"error": "None of your holdings has prices across this whole range yet.", "excluded": excluded}

    covered = sum(weights.values())
    w = pd.Series(weights) / covered  # today's mix among the holdings with enough history
    frame = pd.concat(series, axis=1, sort=True).ffill()  # a holding's last close carried over its exchange's holidays
    returns = frame.pct_change().iloc[1:].fillna(0.0)  # within the window: only exchange holidays, a true no-move
    port = returns[w.index].mul(w, axis=1).sum(axis=1)
    if len(port) < 20:
        return {"error": "Too few days in this range to measure.", "excluded": excluded}
    # Annualised by the days with prices per year in this calendar, not a fixed 252: one holding traded every
    # day (crypto) adds weekend rows with no move for the others, and √252 would then understate the swings.
    per_year = len(port) / ((port.index[-1] - frame.index[0]).days / 365.25)

    var = float(port.var(ddof=1))
    # wealth starts at 1 on the window's first day, so a fall on the very first day of returns counts too
    wealth = pd.concat([pd.Series([1.0], index=[frame.index[0]]), (1 + port).cumprod()])
    dd = _drawdown(wealth)
    trough = dd.idxmin()
    peak = wealth.loc[:trough].idxmax()
    monthly = (1 + port).groupby(port.index.to_period("M")).prod() - 1
    # Only whole calendar months: the first one counts when the window starts with the previous month's last
    # close (its first move is then the month's first day); the last one not while it is still running, which
    # on its last day means until that day's close is in
    whole_first = frame.index[0].to_period("M") < port.index[0].to_period("M")
    running = monthly.index[-1] == end.to_period("M") and not (end.is_month_end and port.index[-1] < end)
    full_months = monthly.iloc[(0 if whole_first else 1):(-1 if running else None)]
    worst_month = full_months.idxmin() if not full_months.empty else None

    per_holding = []
    for iid in w.index:
        r = returns[iid]
        own = series[iid]
        share = float(w[iid] * r.cov(port) / var) if var > 0 else None
        per_holding.append({
            "id": iid, "name": next(h["name"] for h in held if h["id"] == iid), "weight": weights[iid],
            "mix_weight": float(w[iid]),  # its share among the holdings measured, which the risk shares add up over
            "volatility": float(r.std(ddof=1) * math.sqrt(per_year)),
            "max_drawdown": float(_drawdown(own / own.iloc[0]).min()),
            "risk_share": share,
        })
    per_holding.sort(key=lambda h: h["risk_share"] if h["risk_share"] is not None else -1, reverse=True)

    return {
        "from": port.index[0].date().isoformat(), "to": port.index[-1].date().isoformat(), "observations": len(port),
        "covered_weight": covered, "excluded": excluded,
        "volatility": float(math.sqrt(var) * math.sqrt(per_year)), "per_year": per_year,
        "max_drawdown": {"value": float(dd.min()), "peak": peak.date().isoformat(), "trough": trough.date().isoformat()},
        "worst_day": {"value": float(port.min()), "date": port.idxmin().date().isoformat()},
        "worst_month": ({"value": float(full_months.min()), "month": str(worst_month)} if worst_month is not None else None),
        **dict(zip(("beta", "beta_note"), _beta(port, base, fx_cache))),
        "holdings": per_holding,
        "drawdown": [{"date": d.date().isoformat(), "value": round(float(v), 6)} for d, v in dd.items()],
    }


def _beta(port: pd.Series, base: str, fx_cache: dict) -> tuple[dict | None, str | None]:
    """Beta and correlation against MSCI World, in the reporting currency (EUR when no rate is stored).

    On weekly returns: the benchmark (Amsterdam) closes hours before New York, so daily moves of US
    holdings land a day apart and daily figures understate how much they move together.
    Returns (beta, None) or (None, why there is none). Beta covers only the weeks with benchmark prices: its
    own "from" date says so when that is later than the window's start."""
    symbol, label, ccy = BENCHMARK
    bench = research._series(symbol)
    if bench.empty:
        return None, f"no {label} prices stored yet"
    bench = bench[~bench.index.duplicated(keep="last")].sort_index()
    factor = _fx_factor(ccy, base, bench.index, fx_cache)
    in_ccy = base if factor is not None else ccy
    if factor is not None:
        bench = (bench * factor).dropna()
    # only up to the benchmark's last price (a stale series carried forward would read as weeks of no move),
    # and inside its range only over holidays
    wealth = (1 + port).cumprod()
    wealth = wealth[wealth.index <= bench.index[-1]]
    if wealth.empty:
        return None, f"no {label} prices in this range"
    weekly_p = wealth.resample("W-FRI").last().pct_change()
    weekly_b = _carry(bench, wealth.index, COVERAGE_GAP).resample("W-FRI").last().pct_change(fill_method=None)
    both = pd.concat([weekly_p, weekly_b], axis=1, keys=["p", "b"]).dropna()
    if len(both) < 20:
        return None, f"needs 20 weeks with {label} prices; {len(both)} so far"
    if float(both["b"].var(ddof=1)) == 0 or float(both["p"].var(ddof=1)) == 0:
        return None, "the mix did not move" if float(both["p"].var(ddof=1)) == 0 else f"{label} did not move"
    # the dates the weekly returns really span (the week labels are Fridays, often still ahead)
    week_end = wealth.index.to_series().resample("W-FRI").last()
    first = week_end.get(both.index[0] - pd.Timedelta(days=7), pd.NaT)
    first = wealth.index[0] if pd.isna(first) else first
    last = week_end[both.index[-1]]
    beta = float(both["p"].cov(both["b"]) / both["b"].var(ddof=1))
    corr = float(both["p"].corr(both["b"]))
    if not (np.isfinite(beta) and np.isfinite(corr)):
        return None, "could not be computed"
    return {"value": beta, "correlation": corr, "benchmark": label, "currency": in_ccy,
            "from": first.date().isoformat(), "to": last.date().isoformat(), "weeks": len(both)}, None


def risk_report(snapshot: dict, days: int) -> dict:
    rows = snapshot.get("rows", [])
    base = snapshot.get("base_currency", "HUF")
    return {"base_currency": base, "concentration": concentration(rows), "unvalued": unvalued(rows),
            "exposure": exposure(rows), "history": history(rows, base, days)}
