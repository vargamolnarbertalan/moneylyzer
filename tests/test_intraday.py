"""Intraday chart prices: fetched from Yahoo on demand, 4-hour bars built from hourly ones, a short cache."""
import unittest
from unittest import mock

import pandas as pd

from analyzer import market


def fake_ticker(index, closes, currency="USD"):
    t = mock.Mock()
    frame = pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(index, name="Datetime"))
    t.history.return_value = frame
    t.history_metadata = {"currency": currency}
    return t


class IntradayTests(unittest.TestCase):
    def setUp(self):
        market._intraday_cache.clear()
        self.addCleanup(market._intraday_cache.clear)

    def test_bars_come_back_in_utc_and_are_cached_for_a_minute(self):
        stamps = pd.date_range("2026-11-24 09:30", periods=3, freq="15min", tz="America/New_York")
        t = fake_ticker(stamps, [100.0, 101.0, 102.5])
        with mock.patch("analyzer.market.yf.Ticker", return_value=t):
            frame, ccy, err = market.download_intraday("AAPL", "15m", 8)
            again, _, _ = market.download_intraday("AAPL", "15m", 8)
        self.assertIsNone(err)
        self.assertEqual(ccy, "USD")
        self.assertEqual(str(frame["ts"].iloc[0]), "2026-11-24 14:30:00+00:00")
        self.assertEqual(frame["close"].tolist(), [100.0, 101.0, 102.5])
        self.assertIs(again, frame)
        self.assertEqual(t.history.call_count, 1)  # the second call was served from the cache
        self.assertEqual(t.history.call_args.kwargs["interval"], "15m")

    def test_four_hour_bars_are_built_from_hourly_ones(self):
        stamps = pd.date_range("2026-11-24 08:00", periods=8, freq="1h", tz="UTC")
        t = fake_ticker(stamps, [1, 2, 3, 4, 5, 6, 7, 8])
        with mock.patch("analyzer.market.yf.Ticker", return_value=t):
            frame, _, _ = market.download_intraday("VWCE.DE", "4h", 92)
        self.assertEqual(t.history.call_args.kwargs["interval"], "60m")
        self.assertEqual(frame["close"].tolist(), [4, 8])  # each bucket closes at its last hour
        self.assertEqual([str(s) for s in frame["ts"]], ["2026-11-24 08:00:00+00:00", "2026-11-24 12:00:00+00:00"])

    def test_the_span_is_capped_to_what_yahoo_serves(self):
        stamps = pd.date_range("2026-11-24 14:30", periods=2, freq="1min", tz="UTC")
        t = fake_ticker(stamps, [1.0, 2.0])
        with mock.patch("analyzer.market.yf.Ticker", return_value=t):
            market.download_intraday("AAPL", "1m", 8)
        kwargs = t.history.call_args.kwargs
        self.assertEqual(kwargs["end"] - kwargs["start"], pd.Timedelta(days=7))  # 1-minute bars: 7 days a request

    def test_failures_are_reported_and_not_cached(self):
        t = fake_ticker([], [])
        with mock.patch("analyzer.market.yf.Ticker", return_value=t):
            frame, ccy, err = market.download_intraday("NOPE", "1h", 31)
        self.assertTrue(frame.empty)
        self.assertIn("no prices", err)
        self.assertEqual(market._intraday_cache, {})
        broken = mock.Mock()
        broken.history.side_effect = RuntimeError("curl: (7) Failed to connect to query2.finance.yahoo.com:443")
        with mock.patch("analyzer.market.yf.Ticker", return_value=broken), self.assertLogs("lia", "WARNING"):
            self.assertEqual(market.download_intraday("AAPL", "1h", 31)[2], "Yahoo Finance could not be reached. Try again in a minute.")

    def test_four_hour_bars_start_at_each_trading_days_open(self):
        # a US stock in winter: hourly bars from 09:30 New York (14:30 UTC), seven a day
        day1 = pd.date_range("2026-11-23 09:30", periods=7, freq="1h", tz="America/New_York")
        day2 = pd.date_range("2026-11-24 09:30", periods=7, freq="1h", tz="America/New_York")
        t = fake_ticker(day1.append(day2), list(range(1, 15)))
        with mock.patch("analyzer.market.yf.Ticker", return_value=t):
            frame, _, _ = market.download_intraday("AAPL", "4h", 31)
        self.assertEqual([str(s) for s in frame["ts"]], ["2026-11-23 14:30:00+00:00", "2026-11-23 18:30:00+00:00",
                                                         "2026-11-24 14:30:00+00:00", "2026-11-24 18:30:00+00:00"])
        self.assertEqual(frame["close"].tolist(), [4, 7, 11, 14])

    def test_the_next_minutes_refresh_always_gets_new_bars(self):
        stamps = pd.date_range("2026-11-24 14:30", periods=2, freq="15min", tz="UTC")
        t = fake_ticker(stamps, [1.0, 2.0])
        clock = iter([0.0, 2.0, 50.0])  # first fetch starts at 0 (takes 2 s); the refresh comes 50 s later
        with mock.patch("analyzer.market.yf.Ticker", return_value=t), mock.patch("analyzer.market.time.monotonic", lambda: next(clock)):
            market.download_intraday("AAPL", "15m", 8)
            market.download_intraday("AAPL", "15m", 8)  # 2 s later: cached
            market.download_intraday("AAPL", "15m", 8)  # 50 s after the first fetch started: fetched again
        self.assertEqual(t.history.call_count, 2)


class PortfolioIntradayTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from analyzer import db

        self.db = db
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3")):
            p.start()
            self.addCleanup(p.stop)
        db.initialize()

        def add(iid, symbol, currency, close, quantity=0.0, asset="stock"):
            db.upsert_instrument({"instrument_id": iid, "name": iid, "asset_type": asset, "quantity": quantity,
                                  "currency": currency, "market_symbol": symbol, "price_divisor": 1})
            db.save_prices(iid, pd.DataFrame({"date": ["2026-11-20"], "close": [close], "currency": [currency]}), "t")

        add("AAPL", "AAPL", "USD", 95.0)
        add("MSFT", "MSFT", "USD", 200.0, quantity=1)            # Yahoo fails for it: its stored close is used
        add("FUND", "fund:HU0000702709", "HUF", 1000.0, quantity=10, asset="mutual_fund")  # daily NAVs only
        db.add_lots([{"instrument_id": "AAPL", "buy_date": "2026-11-01", "quantity": 2, "unit_price": 90}])
        db.save_fx_usd("HUF", pd.DataFrame({"date": ["2026-11-01"], "per_usd": [340.0]}), "t")
        now = pd.Timestamp.now(tz="UTC").floor("h")
        self.t1, self.t2 = now - pd.Timedelta(hours=2), now - pd.Timedelta(hours=1)

    def fake_download(self, symbol, interval, days):
        bars = {"AAPL": [100.0, 110.0], "USDHUF=X": [350.0, 350.0]}
        if symbol not in bars:
            return pd.DataFrame(columns=["ts", "close"]), None, "Yahoo returned no prices for this interval."
        return pd.DataFrame({"ts": [self.t1, self.t2], "close": bars[symbol]}), "USD", None

    def test_the_total_at_each_bar(self):
        from analyzer import portfolio

        with mock.patch("analyzer.market.download_intraday", self.fake_download):
            out = portfolio.intraday_value_history(8, "15m", "HUF")
        # 2 AAPL x price x 350 HUF/USD + 1 MSFT x 200 x 350 (stored close) + 10 FUND x 1000 HUF
        self.assertEqual([p["value"] for p in out["points"]], [2 * 100 * 350 + 70_000 + 10_000, 2 * 110 * 350 + 70_000 + 10_000])
        self.assertEqual(out["points"][0]["t"], self.t1.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(out["daily_only"], ["MSFT"])
        self.assertEqual(sorted(out["included"]), ["AAPL", "FUND", "MSFT"])

    def test_the_curve_starts_when_every_holding_has_a_price(self):
        from analyzer import portfolio

        self.db.upsert_instrument({"instrument_id": "NEW", "name": "NEW", "asset_type": "stock", "quantity": 1,
                                   "currency": "USD", "market_symbol": "NEW", "price_divisor": 1})  # no stored close

        def download(symbol, interval, days):
            if symbol == "NEW":  # trades only from the second bar on
                return pd.DataFrame({"ts": [self.t2], "close": [50.0]}), "USD", None
            return self.fake_download(symbol, interval, days)

        with mock.patch("analyzer.market.download_intraday", download):
            out = portfolio.intraday_value_history(8, "15m", "HUF")
        self.assertEqual([p["t"] for p in out["points"]], [self.t2.strftime("%Y-%m-%dT%H:%M:%SZ")])
        self.assertEqual(out["points"][0]["value"], 2 * 110 * 350 + 70_000 + 10_000 + 50 * 350)

    def test_review_fixes(self):
        """A holding bought mid-window, Yahoo bars in another price convention, a currency with no rate at all."""
        from analyzer import portfolio

        db = self.db
        t1 = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(days=3)
        t2 = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=1)
        buy_day = t2.tz_convert(None).normalize()
        # a bond bought on t2's day, priced only from then: held 0 units at t1, so t1 must stay on the curve
        db.upsert_instrument({"instrument_id": "BOND", "name": "BOND", "asset_type": "government_bond", "quantity": 0,
                              "currency": "HUF", "market_symbol": None, "price_divisor": 1})
        db.save_prices("BOND", pd.DataFrame({"date": [buy_day - pd.Timedelta(days=1)], "close": [100.0], "currency": ["HUF"]}), "t")
        db.add_lots([{"instrument_id": "BOND", "buy_date": buy_day.date().isoformat(), "quantity": 5, "unit_price": 100}])
        # Yahoo answers in pence for a holding kept in pounds with divisor 1: its stored close is used instead
        db.upsert_instrument({"instrument_id": "VOD", "name": "VOD", "asset_type": "stock", "quantity": 10,
                              "currency": "GBP", "market_symbol": "VOD.L", "price_divisor": 1})
        db.save_prices("VOD", pd.DataFrame({"date": ["2026-11-01"], "close": [1.0], "currency": ["GBP"]}), "t")
        # a EUR holding with no EUR rate anywhere: left out, not the whole chart
        db.upsert_instrument({"instrument_id": "SAP", "name": "SAP", "asset_type": "stock", "quantity": 1,
                              "currency": "EUR", "market_symbol": "SAP.DE", "price_divisor": 1})
        db.save_prices("SAP", pd.DataFrame({"date": ["2026-11-01"], "close": [200.0], "currency": ["EUR"]}), "t")
        db.save_fx_usd("GBP", pd.DataFrame({"date": ["2026-11-01"], "per_usd": [0.8]}), "t")

        def download(symbol, interval, days):
            rows = {"AAPL": ([100.0, 110.0], "USD"), "USDHUF=X": ([350.0, 350.0], "USD"), "VOD.L": ([120.0, 121.0], "GBp"),
                    "SAP.DE": ([200.0, 201.0], "EUR")}
            if symbol not in rows:
                return pd.DataFrame(columns=["ts", "close"]), None, "Yahoo returned no prices for this interval."
            return pd.DataFrame({"ts": [t1, t2], "close": rows[symbol][0]}), rows[symbol][1], None

        with mock.patch("analyzer.market.download_intraday", download):
            out = portfolio.intraday_value_history(8, "1h", "HUF")
        self.assertEqual(len(out["points"]), 2)  # t1 kept: the bond held 0 units then
        vod = 10 * 1.0 / 0.8 * 350  # 10 x 1 GBP (stored close, not 120 "pounds") x HUF per GBP
        first = 2 * 100 * 350 + 70_000 + 10_000 + vod
        self.assertAlmostEqual(out["points"][0]["value"], round(first, 2), places=1)
        self.assertAlmostEqual(out["points"][1]["value"], round(2 * 110 * 350 + 70_000 + 10_000 + vod + 5 * 100, 2), places=1)
        self.assertNotIn("SAP", out["included"])
        self.assertIn("VOD", out["daily_only"])
        self.assertNotIn("SAP", out["daily_only"])  # not counted, so not named as counted

    def test_a_portfolio_without_yahoo_tickers_says_so(self):
        from analyzer import portfolio

        for iid in ("AAPL", "MSFT"):
            self.db.delete_instrument(iid)
        out = portfolio.intraday_value_history(8, "15m", "HUF")
        self.assertEqual(out["points"], [])
        self.assertIn("No holding has minute or hour prices", out["error"])


if __name__ == "__main__":
    unittest.main()
