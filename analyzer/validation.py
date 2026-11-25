from __future__ import annotations

import math
import re

import pandas as pd

from analyzer.db import ASSET_TYPE_CODES

CURRENCY_RE = re.compile(r"[A-Z]{3}")


def _blank(value) -> bool:
    return value is None or (not isinstance(value, str) and pd.isna(value))


def _text(value) -> str:
    """CSV cell as stripped text; blank cells (NaN/None) become ''."""
    # lone UTF-16 surrogates are dropped: SQLite cannot store them and JSON responses cannot encode them
    return "" if _blank(value) else str(value).encode("utf-8", "ignore").decode("utf-8").strip()


def validate_holdings(frame: pd.DataFrame) -> list[dict]:
    required = {"instrument_id", "name", "asset_type", "quantity", "currency"}
    missing = required - set(frame.columns)
    if missing == required:
        raise ValueError(
            "the first line must be the header row: "
            "instrument_id,name,asset_type,quantity,avg_cost,currency,market_symbol,price_divisor,notes "
            "(comma-separated, as in the template)"
        )
    if missing:
        raise ValueError("Holdings CSV is missing columns: " + ", ".join(sorted(missing)))
    clean = frame.copy()
    optional_defaults = {"avg_cost": None, "market_symbol": None, "price_divisor": 1, "notes": ""}
    for col, default in optional_defaults.items():
        if col not in clean.columns:
            clean[col] = default
    rows = []
    for line, row in enumerate(clean.to_dict("records"), start=2):  # line 1 is the CSV header
        try:
            rows.append(_validate_row(row))
        except ValueError as exc:
            raise ValueError(f"CSV line {line}: {exc}") from None
    return rows


def _validate_row(row: dict) -> dict:
    row["instrument_id"] = _text(row.get("instrument_id"))
    row["name"] = _text(row.get("name"))
    row["asset_type"] = _text(row.get("asset_type")).lower()
    row["currency"] = _text(row.get("currency")).upper()
    if not row["instrument_id"] or not row["name"] or not row["currency"]:
        raise ValueError("instrument_id, name and currency must not be empty")
    if row["asset_type"] not in ASSET_TYPE_CODES:
        raise ValueError(f"Unsupported asset_type '{row['asset_type']}' for {row['instrument_id']}. Use: {', '.join(ASSET_TYPE_CODES)}.")
    row["quantity"] = float(row["quantity"])
    if pd.isna(row.get("price_divisor")) or row.get("price_divisor") in (None, ""):
        row["price_divisor"] = 1
    row["price_divisor"] = float(row["price_divisor"])
    if pd.isna(row["quantity"]) or row["quantity"] < 0 or pd.isna(row["price_divisor"]) or row["price_divisor"] <= 0:
        raise ValueError("quantity must be non-negative and price_divisor must be greater than zero")
    avg_cost = _text(row.get("avg_cost"))
    row["avg_cost"] = float(avg_cost) if avg_cost else None
    row["market_symbol"] = _text(row.get("market_symbol")) or None
    row["notes"] = "" if _blank(row.get("notes")) else row["notes"]
    return row


LOTS_HEADER = "instrument_id,buy_date,side,quantity,unit_price,paid_amount,paid_currency,platform,notes"


def validate_lots(frame: pd.DataFrame, known_ids: set[str], default_currency: str = "HUF") -> list[dict]:
    """Transactions CSV: instrument_id, buy_date, quantity, unit_price; optional side, paid_amount, paid_currency, platform, notes.

    side is buy or sell (any case; empty means buy), and buy_date is the trade date. paid_amount is what was
    actually paid, fees included, for a buy, or what was received, net of fees, for a sell; in paid_currency
    (default: the reporting currency). A legacy `cost_huf` column is accepted as paid_amount in HUF.
    Whether a sale is larger than the units held then is checked when the rows are stored (db.add_lots).
    """
    required = {"instrument_id", "buy_date", "quantity", "unit_price"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            "Transactions CSV is missing columns: " + ", ".join(sorted(missing))
            + ". The first line must be: " + LOTS_HEADER
        )
    rows = []
    for line, row in enumerate(frame.to_dict("records"), start=2):
        instrument_id = _text(row.get("instrument_id"))
        if instrument_id not in known_ids:
            raise ValueError(f"CSV line {line}: unknown instrument_id '{instrument_id}'; import the holding first")
        side = _text(row.get("side")).lower() or "buy"
        if side not in ("buy", "sell"):
            raise ValueError(f"CSV line {line}: side must be buy or sell (empty means buy)")
        date_text = _text(row.get("buy_date"))
        if not date_text:
            raise ValueError(f"CSV line {line}: buy_date is empty (use YYYY-MM-DD)")
        try:
            buy_date = pd.to_datetime(date_text, format="%Y-%m-%d").date()
            quantity = float(_text(row.get("quantity")))
            price_text = _text(row.get("unit_price"))
            unit_price = float(price_text) if price_text else 0.0
            legacy = _text(row.get("cost_huf"))
            paid_text = _text(row.get("paid_amount")) or legacy
            paid = float(paid_text) if paid_text else None
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CSV line {line}: {exc}") from None
        if paid is not None and paid <= 0:
            paid = None  # spreadsheets write 0 for an empty cell: treat as "not given", never "free"
        paid_ccy = (_text(row.get("paid_currency")) or ("HUF" if legacy and not _text(row.get("paid_amount")) else default_currency)).upper()
        if paid is not None and not CURRENCY_RE.fullmatch(paid_ccy):
            raise ValueError(f"CSV line {line}: paid_currency must be a 3-letter code like EUR or HUF")
        if not all(math.isfinite(v) for v in (quantity, unit_price, paid or 0.0)):
            raise ValueError(f"CSV line {line}: numbers must be finite")
        if quantity <= 0 or unit_price < 0:
            raise ValueError(f"CSV line {line}: quantity must be > 0 and unit_price must not be negative")
        if unit_price == 0 and paid is None:
            raise ValueError(f"CSV line {line}: give unit_price or paid_amount (what you {'received' if side == 'sell' else 'paid'})")
        if buy_date > pd.Timestamp.today().date():
            raise ValueError(f"CSV line {line}: buy_date {buy_date} is in the future")
        notes, platform = _text(row.get("notes")), _text(row.get("platform"))
        if len(notes) > 500 or len(platform) > 60:
            raise ValueError(f"CSV line {line}: notes may be at most 500 and platform at most 60 characters")
        rows.append({"instrument_id": instrument_id, "buy_date": buy_date.isoformat(), "side": side, "quantity": quantity,
                     "unit_price": unit_price, "paid_amount": paid, "paid_currency": paid_ccy if paid is not None else None,
                     "notes": notes, "platform": platform or None})
    return rows
