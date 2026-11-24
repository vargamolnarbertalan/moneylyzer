"""Create a fictional demo portfolio for screenshots and trying the app out.

Usage (writes ONLY into the given, preferably empty, data directory):
    python scripts/seed_demo.py <data-dir> [--user demo --password <pw>]

Holdings are public tickers with invented quantities; purchase prices are the real historical
closes on the invented buy dates. Needs internet (Yahoo Finance, OTP Alapkezelő).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEMO = [  # instrument_id, name, type, currency, symbol, [(buy_date, quantity), ...]
    ("VWCE", "Vanguard FTSE All-World", "etf", "EUR", "VWCE.DE", [("2024-03-04", 60), ("2025-04-08", 45)]),
    ("AAPL", "Apple", "stock", "USD", "AAPL", [("2024-06-03", 12)]),
    ("NVDA", "NVIDIA", "stock", "USD", "NVDA", [("2024-02-12", 25), ("2025-01-27", 10)]),
    ("SAP", "SAP", "stock", "EUR", "SAP.DE", [("2024-10-01", 9)]),
    ("ASML", "ASML Holding", "stock", "EUR", "ASML.AS", [("2025-08-04", 2)]),
    ("OTP", "OTP Bank", "stock", "HUF", "OTP.BD", [("2024-11-15", 40)]),
    ("SEMI", "iShares MSCI Global Semiconductors", "etf", "EUR", "SEC0.DE", [("2025-04-22", 220)]),
    ("GOLD", "Gold (troy ounce)", "commodity", "USD", "GC=F", [("2024-05-02", 0.4)]),
    ("OTPCEE", "OTP Central-European Equity Fund B", "mutual_fund", "HUF", "fund:HU0000728670", [("2023-09-01", 300000)]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--user", default="demo")
    ap.add_argument("--password")
    args = ap.parse_args()
    data_dir = Path(args.data_dir).resolve()
    if (data_dir / "investment_analyzer.sqlite3").exists():
        print(f"{data_dir} already has a database; use an empty directory.", file=sys.stderr)
        return 1
    os.environ["LIA_DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    import pandas as pd
    from analyzer import advisor, db, market, research
    from server import auth

    db.initialize()
    research.init_tables()
    advisor.init_tables()
    auth.init()

    for iid, name, kind, ccy, symbol, lots in DEMO:
        db.upsert_instrument({"instrument_id": iid, "name": name, "asset_type": kind, "quantity": 0,
                              "currency": ccy, "market_symbol": symbol, "price_divisor": 1})
        isin = market.fund_isin(symbol)
        if isin:
            frame, source, err = market.download_fund_nav(isin, "2022-01-01")
        else:
            frame, err = market.download_daily_history(symbol, period="5y")
            source = "Yahoo Finance via yfinance"
        if err:
            print(f"  {iid}: price download failed: {err}", file=sys.stderr)
            continue
        db.save_prices(iid, frame, source)
        prices = db.get_prices(iid).set_index("date")["close"]
        rows = []
        for day, qty in lots:
            close = prices[prices.index <= pd.Timestamp(day)]
            if close.empty:
                continue
            rows.append({"instrument_id": iid, "buy_date": day, "quantity": qty, "unit_price": round(float(close.iloc[-1]), 4),
                         "notes": "demo"})
        db.add_lots(rows)
        print(f"  {iid}: {len(frame)} prices, {len(rows)} purchases")

    for ccy in ("EUR", "HUF"):
        rates, err = market.download_fx_history(ccy)
        if not err:
            db.save_fx_usd(ccy, rates, "Yahoo Finance via yfinance")

    if args.password:
        code = auth._setup_code
        auth.setup(args.user, args.password, code)
        print(f"Account '{args.user}' created.")
    print(f"Demo portfolio ready in {data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
