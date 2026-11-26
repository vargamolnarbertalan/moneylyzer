"""Risk page figures: concentration, exposure, and the history of today's mix from stored prices."""
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from analyzer import db, research, risk


def row(iid, weight, currency="HUF", kind="stock", closed=False):
    return {"id": iid, "name": iid, "weight": weight, "currency": currency, "type": kind, "closed": closed}


class ConcentrationTests(unittest.TestCase):
    def test_largest_top3_and_effective_number(self):
        rows = [row("A", 0.5), row("B", 0.2), row("C", 0.2), row("D", 0.1), row("X", None, closed=True), row("Q", None)]
        self.assertEqual(risk.unvalued(rows), ["Q"])  # still held, no value: named; the closed one is not
        c = risk.concentration(rows)
        self.assertEqual(c["largest"]["id"], "A")
        self.assertAlmostEqual(c["top3"], 0.9)
        self.assertAlmostEqual(c["effective"], 1 / (0.25 + 0.04 + 0.04 + 0.01))
        self.assertEqual(c["holdings"], 4)
        self.assertIsNone(risk.concentration([]))

    def test_exposure_by_currency_and_type(self):
        e = risk.exposure([row("A", 0.6, "USD", "etf"), row("B", 0.3, "HUF"), row("C", 0.1, "USD")])
        self.assertEqual([(x["key"], round(x["weight"], 6)) for x in e["currency"]], [("USD", 0.7), ("HUF", 0.3)])
        self.assertEqual(e["type"][0]["key"], "etf")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3")):
            p.start()
            self.addCleanup(p.stop)
        db.initialize()
        research.init_tables()
        self.days = pd.bdate_range(end="2026-11-20", periods=300)

    def add(self, iid, closes, currency="HUF", dates=None):
        """Prices on `dates`, or on the last len(closes) weekdays up to the test's today."""
        db.upsert_instrument({"instrument_id": iid, "name": iid, "asset_type": "stock", "quantity": 1,
                              "currency": currency, "market_symbol": None, "price_divisor": 1})
        dates = self.days[-len(closes):] if dates is None else dates
        db.save_prices(iid, pd.DataFrame({"date": dates, "close": closes, "currency": currency}), "t")

    def test_a_known_series(self):
        rng = np.random.default_rng(7)
        a = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(self.days)))
        a[200] = a[199] * 0.9  # a 10% crash day, kept from then on
        a[201:] = a[201:] * 0.9
        b = np.full(len(self.days), 50.0)  # flat: no risk
        self.add("A", a)
        self.add("B", b)
        self.add("NEW", np.full(10, 1.0))  # too little history: left out
        research.save_benchmark("IWDA.AS", pd.DataFrame({"date": self.days, "close": a}))  # the same moves as A
        rows = [row("A", 0.5), row("B", 0.4), row("NEW", 0.1)]
        h = risk.history(rows, "HUF", 366, today=pd.Timestamp("2026-11-20"))
        self.assertEqual(h["excluded"], [{"name": "NEW", "reason": f"prices only from {self.days[-10].date().isoformat()}"}])
        self.assertIsNone(h["beta_note"])
        self.assertEqual(h["beta"]["currency"], "EUR")  # no HUF rate stored: the benchmark stays in its own currency
        self.assertAlmostEqual(h["covered_weight"], 0.9)
        in_window = pd.Series(a, index=self.days)[self.days >= pd.Timestamp("2026-11-20") - pd.Timedelta(days=366)]
        port = in_window.pct_change().iloc[1:] * (0.5 / 0.9)  # B adds nothing; weights renormalised
        per_year = len(port) / ((port.index[-1] - in_window.index[0]).days / 365.25)  # ~261 weekdays here
        self.assertAlmostEqual(h["per_year"], per_year, places=9)
        self.assertAlmostEqual(h["volatility"], float(port.std(ddof=1) * math.sqrt(per_year)), places=9)
        self.assertAlmostEqual(h["worst_day"]["value"], float(port.min()), places=9)
        self.assertEqual(h["worst_day"]["date"], self.days[200].date().isoformat())
        # A moves 1:1 with the benchmark: beta about A's weight (weekly compounding of the daily mix shifts it a little)
        self.assertAlmostEqual(h["beta"]["value"], 0.5 / 0.9, delta=0.01)
        self.assertGreater(h["beta"]["correlation"], 0.999)
        shares = {x["id"]: x["risk_share"] for x in h["holdings"]}
        self.assertAlmostEqual(shares["A"], 1.0, places=9)  # all the risk is A's
        self.assertAlmostEqual(shares["B"], 0.0, places=9)
        mix = {x["id"]: x["mix_weight"] for x in h["holdings"]}  # the same 100% the risk shares add up to
        self.assertAlmostEqual(mix["A"], 0.5 / 0.9)
        self.assertAlmostEqual(sum(mix.values()), 1.0)
        self.assertLess(h["max_drawdown"]["value"], -0.09)
        # one point per return plus the window's first day, where the curve starts at 0
        self.assertEqual(len(h["drawdown"]), h["observations"] + 1)
        self.assertEqual(h["drawdown"][0]["value"], 0.0)

    def test_not_enough_history(self):
        self.add("A", np.full(10, 1.0))
        h = risk.history([row("A", 1.0)], "HUF", 366, today=pd.Timestamp("2026-11-20"))
        self.assertEqual(h["error"], "None of your holdings has prices across this whole range yet.")
        self.assertEqual([x["name"] for x in h["excluded"]], ["A"])

    def test_a_holding_without_an_exchange_rate_is_left_out_not_a_crash(self):
        self.add("A", np.linspace(100, 120, len(self.days)))
        self.add("U", np.linspace(10, 12, len(self.days)), currency="USD")  # no USD/HUF rate stored
        h = risk.history([row("A", 0.7), row("U", 0.3, "USD")], "HUF", 366, today=pd.Timestamp("2026-11-20"))
        self.assertEqual(h["excluded"], [{"name": "U", "reason": "no USD/HUF exchange rate in this range"}])
        self.assertAlmostEqual(h["covered_weight"], 0.7)

    def test_a_holding_listed_part_way_through_is_left_out(self):
        rng = np.random.default_rng(3)
        self.add("A", 100 * np.cumprod(1 + rng.normal(0, 0.01, len(self.days))))
        self.add("LATE", 100 * np.cumprod(1 + rng.normal(0, 0.03, 150)))  # ~7 months of a 1-year window
        h = risk.history([row("A", 0.5), row("LATE", 0.5)], "HUF", 366, today=pd.Timestamp("2026-11-20"))
        self.assertEqual(h["excluded"], [{"name": "LATE", "reason": f"prices only from {self.days[-150].date().isoformat()}"}])
        self.assertEqual([x["id"] for x in h["holdings"]], ["A"])

    def test_a_fall_on_the_first_day_counts_in_the_drawdown(self):
        start = pd.Timestamp("2026-11-20") - pd.Timedelta(days=366)
        first = int(np.argmax(self.days >= start))  # the window's first price day
        a = np.full(len(self.days), 100.0)
        a[first + 1:] = 80.0  # down 20% on the first day with a return, flat after
        self.add("A", a)
        h = risk.history([row("A", 1.0)], "HUF", 366, today=pd.Timestamp("2026-11-20"))
        self.assertAlmostEqual(h["max_drawdown"]["value"], -0.2, places=9)
        self.assertEqual(h["max_drawdown"]["peak"], self.days[first].date().isoformat())
        self.assertEqual(h["max_drawdown"]["trough"], self.days[first + 1].date().isoformat())

    def walk(self, n, seed, sd=0.01):
        return 100 * np.cumprod(1 + np.random.default_rng(seed).normal(0, sd, n))

    def history_of(self, rows, today="2026-11-20"):
        return risk.history(rows, "HUF", 366, today=pd.Timestamp(today))

    def test_a_month_without_prices_is_not_one_day_of_movement(self):
        a = self.walk(len(self.days), 1)
        self.add("A", a)
        hole = pd.Series(a, index=self.days).drop(self.days[150:180])  # six weeks missing, server was off
        self.add("H", hole.to_numpy(), dates=hole.index)
        h = self.history_of([row("A", 0.5), row("H", 0.5)])
        self.assertEqual(h["excluded"], [{"name": "H", "reason": "no prices from "
                                          f"{self.days[149].date().isoformat()} to {self.days[180].date().isoformat()}"}])

    def test_a_long_exchange_holiday_is_not_a_gap(self):
        a = pd.Series(self.walk(len(self.days), 11), index=self.days)
        closed = a.drop(a.index[(a.index >= "2026-02-09") & (a.index <= "2026-02-18")])  # Feb 6 -> Feb 19: 13 days
        self.add("CN", closed.to_numpy(), dates=closed.index)
        h = self.history_of([row("CN", 1.0)])
        self.assertEqual(h["excluded"], [])

    def test_a_missing_exchange_rate_is_named_not_the_price(self):
        self.add("A", self.walk(len(self.days), 12))
        self.add("U", self.walk(len(self.days), 13), currency="USD")  # prices all year
        rates = self.days[:-40]  # but the HUF rate stopped ~2 months ago
        db.save_fx_usd("HUF", pd.DataFrame({"date": rates, "per_usd": 350.0}), "t")
        h = self.history_of([row("A", 0.5), row("U", 0.5, "USD")])
        (left,) = h["excluded"]
        self.assertEqual(left["name"], "U")
        since = pd.Timestamp(left["reason"].removeprefix("no USD/HUF rate since "))  # a rate counts for a week
        self.assertTrue(rates[-1] <= since <= rates[-1] + pd.Timedelta(days=7), left["reason"])

    def test_a_finished_month_counts_even_before_the_next_has_prices(self):
        a = np.full(len(self.days), 100.0)
        oct_ = (self.days >= "2026-10-01") & (self.days <= "2026-10-30")
        a[oct_] = np.linspace(100, 80, oct_.sum())  # October: down 20%
        a[self.days > "2026-10-30"] = 80.0
        self.add("A", a)
        h = self.history_of([row("A", 1.0)], today="2026-10-31")  # a Saturday: no November prices yet
        self.assertEqual(h["worst_month"]["month"], "2026-10")
        self.assertAlmostEqual(h["worst_month"]["value"], -0.2, places=9)

    def test_a_window_starting_on_a_month_end_keeps_its_first_month(self):
        a = np.full(len(self.days), 100.0)
        nov = (self.days >= "2025-11-01") & (self.days <= "2025-11-30")
        a[nov] = np.linspace(97, 70, nov.sum())  # November 2025: down 30% from October's last close
        a[self.days > "2025-11-30"] = 70.0
        self.add("A", a)
        h = self.history_of([row("A", 1.0)], today="2026-11-01")  # 1Y starts Fri 2025-10-31, October's last close,
        self.assertEqual(h["worst_month"]["month"], "2025-11")  # so November is whole
        self.assertAlmostEqual(h["worst_month"]["value"], -0.3, places=9)

    def test_a_stale_and_a_sparse_holding_say_what_is_wrong(self):
        self.add("A", self.walk(len(self.days), 2))
        stale = pd.Series(self.walk(len(self.days), 3), index=self.days).iloc[:-60]  # last priced ~3 months ago
        weekly = pd.Series(self.walk(len(self.days), 4), index=self.days).iloc[::5]  # a weekly NAV the whole year
        for iid, s in (("S", stale), ("W", weekly)):
            self.add(iid, s.to_numpy(), dates=s.index)
        h = self.history_of([row("A", 0.4), row("S", 0.3), row("W", 0.3)])
        reasons = {x["name"]: x["reason"] for x in h["excluded"]}
        self.assertEqual(reasons["S"], f"no price since {stale.index[-1].date().isoformat()}")
        self.assertRegex(reasons["W"], r"^only \d+ prices in this range \(needs 60\)$")

    def test_a_holding_traded_every_day_does_not_shrink_the_volatility(self):
        a = self.walk(len(self.days), 6)
        self.add("A", a)
        alone = self.history_of([row("A", 1.0)])
        daily = pd.date_range(end="2026-11-20", periods=420)  # crypto: weekends too
        self.add("C", np.full(len(daily), 5.0), dates=daily)
        mixed = self.history_of([row("A", 0.999), row("C", 0.001)])
        vol = {x["id"]: x["volatility"] for x in mixed["holdings"]}
        self.assertAlmostEqual(vol["A"], alone["volatility"], delta=0.01 * alone["volatility"])
        self.assertAlmostEqual(mixed["volatility"], 0.999 * alone["volatility"], delta=0.01 * alone["volatility"])

    def test_a_holding_without_a_value_is_named(self):
        self.add("A", self.walk(len(self.days), 8))
        rows = [row("A", 1.0), row("Q", None)]
        self.assertEqual(self.history_of(rows)["excluded"],
                         [{"name": "Q", "reason": "no value right now (no price or exchange rate)"}])
        self.assertEqual(risk.unvalued(rows), ["Q"])

    def test_beta_ignores_weeks_after_the_benchmark_went_stale(self):
        a = self.walk(len(self.days), 9)
        self.add("A", a)
        research.save_benchmark("IWDA.AS", pd.DataFrame({"date": self.days[:-65], "close": a[:-65]}))  # ~3 months old
        h = self.history_of([row("A", 1.0)])
        self.assertGreater(h["beta"]["correlation"], 0.999)  # stale weeks would read as no move and pull it down
        self.assertLessEqual(pd.Timestamp(h["beta"]["to"]), self.days[-66])  # a real date, not the week's Friday label
        self.assertGreaterEqual(pd.Timestamp(h["beta"]["from"]), pd.Timestamp(h["from"]) - pd.Timedelta(days=1))
        self.assertLess(h["beta"]["weeks"], 42)

    def test_no_benchmark_gives_a_reason_instead_of_beta(self):
        rng = np.random.default_rng(5)
        self.add("A", 100 * np.cumprod(1 + rng.normal(0, 0.01, len(self.days))))
        h = risk.history([row("A", 1.0)], "HUF", 366, today=pd.Timestamp("2026-11-20"))
        self.assertIsNone(h["beta"])
        self.assertEqual(h["beta_note"], "no MSCI World (IWDA) prices stored yet")


if __name__ == "__main__":
    unittest.main()
