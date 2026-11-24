from __future__ import annotations

import hashlib
from typing import Any, Callable

import numpy as np
import pandas as pd


SUPPORTED_SIGNAL_TYPES = {"stock", "etf", "mutual_fund", "commodity"}


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def rsi(close: pd.Series, period: int = 14) -> float | None:
    delta = close.astype(float).diff()
    gains = delta.clip(lower=0).rolling(period).mean()
    losses = -delta.clip(upper=0).rolling(period).mean()
    if len(close) <= period:
        return None
    avg_gain = gains.iloc[-1]
    avg_loss = losses.iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def analyze_history(instrument: dict, history: pd.DataFrame) -> dict:
    """Compute transparent price-only metrics. Does not use an LLM."""
    asset_type = str(instrument.get("asset_type", ""))
    clean = history[["date", "close"]].copy() if not history.empty else pd.DataFrame(columns=["date", "close"])
    if not clean.empty:
        clean["date"] = pd.to_datetime(clean["date"], errors="coerce")
        clean["close"] = pd.to_numeric(clean["close"], errors="coerce")
        clean = clean.dropna().query("close > 0").drop_duplicates("date").sort_values("date")

    result: dict[str, Any] = {
        "observations": int(len(clean)),
        "as_of": clean["date"].iloc[-1].date().isoformat() if not clean.empty else None,
        "latest_price": _safe_float(clean["close"].iloc[-1]) if not clean.empty else None,
        "return_1m": None,
        "return_3m": None,
        "return_6m": None,
        "return_1y": None,
        "drawdown": None,
        "annual_volatility": None,
        "rsi_14": None,
        "sma_50": None,
        "sma_200": None,
        "score": None,
        "signal": "INSUFFICIENT DATA",
        "reasons": [],
    }
    if clean.empty:
        result["reasons"] = ["No price history is stored for this instrument."]
        return result

    close = clean["close"].astype(float).reset_index(drop=True)
    latest = float(close.iloc[-1])

    for label, periods in (("return_1m", 21), ("return_3m", 63), ("return_6m", 126), ("return_1y", 252)):
        if len(close) > periods:
            result[label] = float(latest / close.iloc[-periods - 1] - 1)

    running_peak = close.cummax().iloc[-1]
    result["drawdown"] = float(latest / running_peak - 1) if running_peak else None
    log_returns = np.log(close / close.shift(1)).dropna()
    if len(log_returns) >= 20:
        result["annual_volatility"] = float(log_returns.tail(63).std(ddof=1) * np.sqrt(252))
    result["rsi_14"] = rsi(close)
    result["sma_50"] = _safe_float(close.tail(50).mean()) if len(close) >= 50 else None
    result["sma_200"] = _safe_float(close.tail(200).mean()) if len(close) >= 200 else None

    if asset_type == "government_bond":
        result["signal"] = "NOT RATED"
        result["reasons"] = [
            "Government bonds are deliberately excluded from price-only BUY/SELL scoring.",
            "A responsible bond assessment needs at least coupon, maturity, yield, accrued interest and currency information.",
        ]
        return result

    if asset_type not in SUPPORTED_SIGNAL_TYPES:
        result["signal"] = "NOT RATED"
        result["reasons"] = ["This asset type is not covered by the current technical signal rules."]
        return result

    if len(close) < 200:
        result["signal"] = "INSUFFICIENT DATA"
        result["reasons"] = [f"At least 200 daily observations are required; currently {len(close)} are stored."]
        return result

    momentum_21 = result["return_1m"]
    momentum_63 = result["return_3m"]
    current_rsi = result["rsi_14"]
    checks = [
        (latest > result["sma_200"], "Latest price is above its 200-day average."),
        (result["sma_50"] > result["sma_200"], "50-day average is above the 200-day average."),
        (momentum_63 is not None and momentum_63 > 0, "Three-month momentum is positive."),
        (momentum_21 is not None and momentum_21 > 0, "One-month momentum is positive."),
        (current_rsi is not None and 35 <= current_rsi <= 70, "14-day RSI is in a broad, non-extreme range (35–70)."),
    ]
    score = sum(bool(passed) for passed, _ in checks)
    result["score"] = score
    result["reasons"] = [description for passed, description in checks if passed]
    failed = [description.replace(" is ", " is not ", 1) for passed, description in checks if not passed]
    result["failed_checks"] = failed

    if score >= 4:
        result["signal"] = "BUY CANDIDATE"
    elif score == 3:
        result["signal"] = "WATCH"
    elif score == 2:
        result["signal"] = "HOLD"
    else:
        result["signal"] = "SELL / REVIEW"
    result["reasons"] = result["reasons"] + failed
    result["signal_note"] = "Price-trend heuristic only; it does not assess business fundamentals, valuation, taxes or your personal circumstances."
    return result


def bootstrap_scenarios(
    instrument_id: str,
    history: pd.DataFrame,
    horizons: tuple[int, ...] = (21, 63, 126),
    simulations: int = 2500,
) -> list[dict]:
    """Historical bootstrap ranges in trading observations, not price predictions."""
    if history.empty or len(history) < 61:
        return []
    close = pd.to_numeric(history["close"], errors="coerce").dropna().astype(float)
    close = close[close > 0]
    log_returns = np.log(close / close.shift(1)).dropna().tail(756).to_numpy()
    if len(log_returns) < 60:
        return []

    seed = int(hashlib.sha256(instrument_id.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    current = float(close.iloc[-1])
    output = []
    max_horizon = max(horizons)
    sampled = rng.choice(log_returns, size=(simulations, max_horizon), replace=True)
    cumulative = np.cumsum(sampled, axis=1)

    for horizon in horizons:
        projected = current * np.exp(cumulative[:, horizon - 1])
        q10, q50, q90 = np.quantile(projected, [0.10, 0.50, 0.90])
        output.append(
            {
                "observations_ahead": horizon,
                "p10": float(q10),
                "median": float(q50),
                "p90": float(q90),
                "change_p10": float(q10 / current - 1),
                "change_median": float(q50 / current - 1),
                "change_p90": float(q90 / current - 1),
            }
        )
    return output


# Yahoo quotes these markets in minor units (pence, cents, agorot): 1/100 of the main currency.
# Minor units Yahoo quotes in (case matters: "GBp" is pence) -> (major currency, units per major).
MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}
MINOR_UNIT_CURRENCIES = set(MINOR_UNITS)


def currency_mismatch(holding_currency: Any, price_currency: Any, price_divisor: Any = 1.0) -> bool:
    """True when stored prices are quoted in a different currency (or minor unit) than the holding.

    A minor-unit quote matches a holding in the major currency whose price divisor converts it
    (GBp prices for a GBP holding with divisor 100).
    """
    if price_currency is None or pd.isna(price_currency) or not str(price_currency).strip():
        return False
    price = str(price_currency).strip()
    holding = str(holding_currency).strip().upper()
    if price in MINOR_UNITS:
        major, per = MINOR_UNITS[price]
        try:
            divisor = float(price_divisor or 1.0)
        except (TypeError, ValueError):
            return True
        return not (major == holding and divisor == per)
    return price.upper() != holding


def latest_price_currency(history: pd.DataFrame) -> str | None:
    """Most recent non-blank currency stored with the prices (rows may lack one)."""
    if history.empty or "currency" not in history.columns:
        return None
    known = history["currency"].dropna().astype(str).str.strip()
    known = known[known != ""]
    return known.iloc[-1] if not known.empty else None


def currency_value(quantity: float, price: float, price_divisor: float = 1.0) -> float:
    divisor = float(price_divisor or 1.0)
    if divisor <= 0:
        raise ValueError("price_divisor must be greater than zero")
    return float(quantity) * float(price) / divisor


def summarize_position(
    item: dict,
    lots: list[dict],
    price: float | None,
    price_currency: str | None,
    fx_now: dict[str, float],
    rate_on: Callable[[str, str], float | None],
    base: str = "HUF",
) -> dict:
    """Live value and unrealized P/L of one holding, in its own currency and in the base currency.

    item: instrument row (quantity, avg_cost, currency, price_divisor).
    lots: purchases (buy_date, quantity, unit_price, paid_amount, paid_currency); when present they
          define the quantity and average cost, otherwise the instrument row does.
    fx_now: current base-currency value of one unit per currency (the base itself is implicitly 1).
    rate_on(currency, date): base-currency value of one unit of `currency` on that date, or None.
    A None anywhere means "unknown"; it never silently becomes 0.
    """
    currency = str(item["currency"]).strip().upper()
    base = base.upper()
    divisor = float(item.get("price_divisor") or 1.0)
    out = {
        "quantity": None, "avg_cost": None, "value": None, "value_base": None, "cost": None,
        "cost_base": None, "pnl": None, "pnl_base": None, "pnl_pct": None, "fx_now": None, "check": "",
    }
    notes = []

    def known(x) -> bool:
        return x is not None and not pd.isna(x)

    def rate(ccy: str, day: str) -> float | None:
        return 1.0 if ccy == base else rate_on(ccy, day)

    if lots:
        quantity = sum(float(lot["quantity"]) for lot in lots)
        priced = [lot for lot in lots if known(lot.get("unit_price")) and float(lot["unit_price"]) > 0]
        # Average cost only when every lot has a unit price; a partial average would understate it.
        avg_cost = (
            sum(float(lot["quantity"]) * float(lot["unit_price"]) for lot in priced) / quantity
            if len(priced) == len(lots) else None
        )
        row_qty = item.get("quantity")
        if known(row_qty) and float(row_qty) > 0 and abs(float(row_qty) - quantity) > 1e-9 * max(1.0, quantity):
            notes.append(f"Purchases total {quantity:g} units but the holding row says {float(row_qty):g}; purchases are used")
    else:
        quantity = float(item.get("quantity") or 0.0)
        raw_avg = item.get("avg_cost")
        avg_cost = float(raw_avg) if known(raw_avg) and float(raw_avg) > 0 else None
    out["quantity"], out["avg_cost"] = quantity, avg_cost

    rate_now = 1.0 if currency == base else fx_now.get(currency)
    out["fx_now"] = rate_now
    if avg_cost is not None:
        out["cost"] = quantity * avg_cost / divisor

    # Cost in the base currency: per lot the amount actually paid (any currency) at the buy-date rate,
    # else unit price x quantity at the buy-date rate of the holding's currency.
    if lots:
        total, missing_rate, missing_price = 0.0, set(), 0
        for lot in lots:
            day = str(lot["buy_date"])
            paid = lot.get("paid_amount")
            if known(paid) and float(paid) > 0:
                raw_ccy = lot.get("paid_currency")
                paid_ccy = (str(raw_ccy) if known(raw_ccy) and str(raw_ccy).strip() else currency).strip().upper()
                r = rate(paid_ccy, day)
                if r is None:
                    missing_rate.add(paid_ccy)
                else:
                    total += float(paid) * r
                continue
            unit = lot.get("unit_price")
            if not known(unit) or float(unit) <= 0:
                missing_price += 1
                continue
            r = rate(currency, day)
            if r is None:
                missing_rate.add(currency)
            else:
                total += float(lot["quantity"]) * float(unit) / divisor * r
        if missing_price:
            notes.append(f"{missing_price} purchase(s) without unit price or amount paid: cost unknown")
        if missing_rate:
            notes.append(f"{'/'.join(sorted(missing_rate))}/{base} rate missing for a buy date: cost unknown")
        if not missing_price and not missing_rate:
            out["cost_base"] = total
    elif currency == base and out["cost"] is not None:
        out["cost_base"] = out["cost"]
    else:
        notes.append(f"Add purchases to see P/L in {base}")

    if price is None:
        notes.append("No price yet")
    elif currency_mismatch(currency, price_currency, divisor):
        notes.append(f"Price quoted in {price_currency}, holding in {currency}: value not calculated")
    else:
        out["value"] = currency_value(quantity, price, divisor)
        if out["cost"] is not None:
            out["pnl"] = out["value"] - out["cost"]
        if rate_now is None:
            notes.append(f"No live {currency}/{base} rate")
        else:
            out["value_base"] = out["value"] * rate_now
            if out["cost_base"] is not None:
                out["pnl_base"] = out["value_base"] - out["cost_base"]
                out["pnl_pct"] = out["pnl_base"] / out["cost_base"] if out["cost_base"] else None

    out["check"] = "; ".join(notes)
    return out
