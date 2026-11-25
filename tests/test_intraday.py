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


if __name__ == "__main__":
    unittest.main()
