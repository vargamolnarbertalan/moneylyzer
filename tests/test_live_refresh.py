"""Live refresh: the interval comes from Settings, and Yahoo is polled only while a browser is watching."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from analyzer import db
from server import live


class IntervalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3")):
            p.start()
            self.addCleanup(p.stop)
        db.initialize()

    def test_the_setting_wins_over_the_default_and_only_listed_values_are_taken(self):
        hub = live.LiveHub()
        self.assertEqual(hub.seconds(), live.DEFAULT_LIVE_SECONDS)
        hub.set_seconds(300)
        self.assertEqual(hub.seconds(), 300)
        with self.assertRaises(ValueError):
            hub.set_seconds(5)  # Yahoo throttles anything faster than 15 s
        db.set_setting("live_seconds", "7")  # a hand-edited database
        self.assertEqual(hub.seconds(), live.DEFAULT_LIVE_SECONDS)


class RequestRefreshTests(unittest.TestCase):
    def test_an_edited_holding_gets_its_history_again_and_quotes_keep_the_15_s_floor(self):
        hub = live.LiveHub()
        hub.seconds = lambda: 900  # type: ignore[method-assign]
        hub._daily_done["daily:AAPL"] = "2026-11-25"  # done today (with the old, mistyped ticker)
        hub._next_try["daily:AAPL"] = time.time() + 600  # or failed and backing off
        hub._fund_done["fund:AAPL"] = time.time()
        hub._last_quotes = time.monotonic() - 5  # quotes fetched 5 s ago
        hub.request_refresh("AAPL")
        self.assertNotIn("daily:AAPL", hub._daily_done)
        self.assertNotIn("daily:AAPL", hub._next_try)
        self.assertNotIn("fund:AAPL", hub._fund_done)
        due = hub._last_quotes + hub.seconds() - time.monotonic()
        self.assertTrue(9 < due <= 10.1, due)  # the next quote pass 15 s after the last one, not 15 min
        hub._last_quotes = time.monotonic() - 3600
        hub.request_refresh("AAPL")
        self.assertLessEqual(hub._last_quotes + hub.seconds() - time.monotonic(), 0)  # long ago: at once


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = live.LiveHub()
        self.interval = 0.2
        self.hub.seconds = lambda: self.interval  # type: ignore[method-assign]
        self.runs = []

        self.background = []

        async def fake_refresh(manual=False, force=False, quotes=True):
            now = time.monotonic()
            (self.runs if quotes else self.background).append(now)
            self.hub.snapshot = {"n": len(self.runs)}
            self.hub._last_background = now
            if quotes:
                self.hub._last_quotes = now
            return self.hub.snapshot

        self.hub.refresh = fake_refresh  # type: ignore[method-assign]
        self.task = asyncio.create_task(self.hub.run_forever())

    async def asyncTearDown(self):
        self.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.task

    async def test_no_viewer_no_polling_and_a_viewer_wakes_it(self):
        await asyncio.sleep(0.6)
        self.assertEqual(len(self.runs), 1)  # the first snapshot at startup, then nothing: nobody is watching
        q = self.hub.subscribe()
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.runs), 2)  # data older than the interval: refreshed as the viewer arrives
        await asyncio.sleep(0.5)
        self.assertGreaterEqual(len(self.runs), 3)  # and every interval while watched (~2 more in 0.5 s)
        self.hub.unsubscribe(q)
        seen = len(self.runs)
        await asyncio.sleep(0.6)
        self.assertLessEqual(len(self.runs), seen + 1)  # at most the cycle already due, then paused again

    async def test_a_new_interval_applies_without_a_restart(self):
        self.hub.subscribe()
        await asyncio.sleep(0.05)
        self.interval = 30  # slow: the loop now sleeps for 30 s
        await asyncio.sleep(0.3)
        seen = len(self.runs)
        self.interval = 0.05
        self.hub._wake.set()  # what set_seconds does after saving
        await asyncio.sleep(0.3)
        self.assertGreater(len(self.runs), seen + 1)  # ~6 at 0.05 s; under 30 s it would be none

    async def test_history_jobs_keep_running_while_nobody_watches(self):
        with mock.patch.object(live, "BACKGROUND_SECONDS", 0.1):
            self.hub._wake.set()
            await asyncio.sleep(0.45)
        self.assertEqual(len(self.runs), 1)  # live quotes: only the startup snapshot
        self.assertGreaterEqual(len(self.background), 2)  # daily history, NAVs, benchmarks, news: no gaps

    async def test_the_loop_outlives_an_error_while_deciding(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("database is locked")
            return self.interval

        self.hub.seconds = flaky  # type: ignore[method-assign]
        with mock.patch.object(live, "LOOP_ERROR_PAUSE", 0.01), self.assertLogs("lia.live", "ERROR"):
            self.hub.subscribe()
            await asyncio.sleep(0.6)
        self.assertGreaterEqual(len(self.runs), 3)  # still refreshing after the error

    async def test_a_viewer_arriving_soon_after_a_refresh_does_not_refetch(self):
        await asyncio.sleep(0.05)  # startup snapshot
        self.interval = 30
        self.hub.subscribe()
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.runs), 1)  # the data is fresh enough: no second request to Yahoo


class DailyHistoryTests(unittest.TestCase):
    """Yahoo's adjusted closes change for all past days after a split or dividend; a month-only update would
    leave the older stored closes on the old scale, a fake 90% fall on the Risk page and the charts."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3")):
            p.start()
            self.addCleanup(p.stop)
        db.initialize()
        db.upsert_instrument({"instrument_id": "N", "name": "N", "asset_type": "stock", "quantity": 1,
                              "currency": "USD", "market_symbol": "NVDA", "price_divisor": 1})
        self.days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=60)
        db.save_prices("N", pd.DataFrame({"date": self.days, "close": 1000.0, "currency": "USD"}), "t")

    def run_daily(self, hub, scale):
        """One daily job where Yahoo serves today's history at `scale` times the stored closes; the periods asked."""
        periods = []

        def download(symbol, period):
            periods.append(period)
            days = self.days[-22:] if period == "1mo" else self.days
            return pd.DataFrame({"date": days, "close": 1000.0 * scale, "currency": "USD"}), None

        with mock.patch.object(live, "download_daily_history", side_effect=download):
            hub._run_job(("daily", "daily:N", "N", "NVDA", None))
        return periods

    def test_full_once_per_run_then_a_month_and_full_again_after_a_split(self):
        hub = live.LiveHub()
        self.assertEqual(self.run_daily(hub, 1.0), ["5y"])  # first pass this run: repairs older seams
        self.assertEqual(self.run_daily(hub, 1.0), ["1mo"])  # unchanged past closes: a month is enough
        self.assertEqual(self.run_daily(hub, 0.1), ["1mo", "5y"])  # a 10:1 split re-adjusted them all
        self.assertEqual(set(db.get_prices("N")["close"]), {100.0})  # no old-scale close left behind
        hub.request_refresh("N")  # another ticker typed in: its whole history again
        self.assertEqual(self.run_daily(hub, 0.1), ["5y"])

    def test_a_repair_reaches_back_to_the_oldest_stored_close(self):
        old = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.DateOffset(years=6), periods=5)
        db.save_prices("N", pd.DataFrame({"date": old, "close": 1000.0, "currency": "USD"}), live.YAHOO)
        self.assertEqual(live.full_period("N")[0], "10y")  # 5y would leave the 6-year-old closes on the old scale

    def test_repairs_dont_grow_the_history_backwards(self):
        hub = live.LiveHub()
        oldest = self.days[0].date().isoformat()

        def download(symbol, period):  # Yahoo serves as much as asked for
            days = pd.bdate_range(end=self.days[-1], periods={"5y": 1300, "10y": 2600}.get(period, 10000))
            return pd.DataFrame({"date": days, "close": 1000.0, "currency": "USD"}), None

        with mock.patch.object(live, "download_daily_history", side_effect=download):
            for _ in range(3):  # three starts: each does a full download
                hub._daily_full.clear()
                hub._run_job(("daily", "daily:N", "N", "NVDA", None))
        self.assertEqual(db.get_prices("N")["date"].min().date().isoformat(), oldest)

    def test_a_full_download_replaces_other_listings_closes_in_its_range(self):
        holiday = self.days[-30] + pd.Timedelta(days=1)  # a Saturday-ish date only the old listing had
        db.save_prices("N", pd.DataFrame({"date": [holiday], "close": [70.0], "currency": "GBp"}), live.YAHOO)
        self.run_daily(live.LiveHub(), 1.0)
        self.assertNotIn(holiday.date().isoformat(), set(db.get_prices("N")["date"]))

    def test_a_pause_longer_than_a_month_gets_the_whole_history(self):
        hub = live.LiveHub()
        hub._daily_full.add("N")
        with mock.patch.object(live, "newest_yahoo", return_value="2026-01-02"):
            self.assertEqual(self.run_daily(hub, 1.0), ["5y"])  # one month would leave a hole behind

    def test_imported_prices_are_kept(self):
        mine = self.days[-10]
        db.save_prices("N", pd.DataFrame({"date": [mine], "close": [777.0], "currency": "USD"}), live.IMPORTED)
        hub = live.LiveHub()
        hub._daily_full.add("N")
        self.assertEqual(self.run_daily(hub, 1.0), ["1mo"])  # their own close is no sign of a re-adjustment
        prices = db.get_prices("N").set_index("date")
        self.assertEqual(prices.loc[mine.date().isoformat(), "close"], 777.0)
        self.assertEqual(prices.loc[mine.date().isoformat(), "source"], live.IMPORTED)

    def test_readjusted_ignores_the_newest_day_and_tiny_rounding(self):
        frame = pd.DataFrame({"date": self.days[-5:], "close": [1000.0, 1000.0005, 1000.0, 1000.0, 990.0]})
        self.assertFalse(live.readjusted("N", frame))  # today's bar still moving when it was stored
        self.assertTrue(live.readjusted("N", frame.assign(close=[995.0] * 5)))  # a 0.5% dividend adjustment


class BenchmarkBackfillTests(unittest.TestCase):
    """The Risk page's 3Y range needs about 3 years of MSCI World; older installs stored only 2."""

    def run_bench(self, hub, years_stored):
        today = pd.Timestamp.today().normalize()
        stored = (pd.Series([1.0, 1.0], index=[today - pd.DateOffset(years=years_stored), today])
                  if years_stored else pd.Series(dtype=float))
        periods = []

        def download(symbol, period):
            periods.append(period)
            return pd.DataFrame({"date": [], "close": []}), None

        with mock.patch.object(live.research, "_series", return_value=stored), \
                mock.patch.object(live.research, "save_benchmark"), \
                mock.patch.object(live, "download_daily_history", side_effect=download):
            hub._run_job(("bench", "bench:IWDA.AS", "IWDA.AS", "IWDA.AS", None))
        return periods[0]

    def test_first_fetch_and_a_short_history_get_5y_once_a_long_one_only_a_month(self):
        hub = live.LiveHub()
        self.assertEqual(self.run_bench(hub, None), "5y")
        hub = live.LiveHub()
        self.assertEqual(self.run_bench(hub, 2), "5y")  # 2 years stored: backfill
        self.assertEqual(self.run_bench(hub, 2), "1mo")  # Yahoo had no more: not again this run
        self.assertEqual(self.run_bench(live.LiveHub(), 5), "1mo")


if __name__ == "__main__":
    unittest.main()
