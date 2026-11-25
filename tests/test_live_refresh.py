"""Live refresh: the interval comes from Settings, and Yahoo is polled only while a browser is watching."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
