"""Regression tests for the 2026-11-24 review of the shared core (engine, market, db, validation)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from analyzer import db, market
from analyzer.engine import summarize_position
from analyzer.validation import validate_lots


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3")]
        for p in self.patches:
            p.start()
        db.initialize()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class QuoteTests(unittest.TestCase):
    def fake(self, md):
        t = mock.Mock()
        t.history_metadata = md
        return mock.patch("analyzer.market.yf.Ticker", return_value=t)

    def test_quote_without_currency_is_refused(self):
        with self.fake({"regularMarketPrice": 9200.0, "regularMarketTime": 1764000000}):
            q = market.live_quote("NOCUR.L")
        self.assertIsNone(q["price"])
        self.assertIn("currency", q["error"])

    def test_quote_time_edge_cases(self):
        self.assertEqual(market.quote_time(np.int64(1764000000)).year, 2025)  # seconds, not nanoseconds
        self.assertIsNone(market.quote_time(pd.NaT))
        self.assertIsNone(market.quote_time(float("nan")))

    def test_isin_with_trailing_newline_rejected(self):
        self.assertEqual(market.fund_isin("fund:HU0000728670\n"), "HU0000728670")  # stripped, clean for the URL
        self.assertIsNone(market.fund_isin("fund:HU0000728670\nX"))
        _, _, err = market.download_fund_nav("HU0000728670\n", "2026-01-01")
        self.assertIn("Not a valid ISIN", err)


class FxHistoryTests(unittest.TestCase):
    def test_window_is_fixed_and_today_dropped(self):
        today = pd.Timestamp.today().normalize()
        idx = pd.DatetimeIndex([today - pd.Timedelta(days=1), today], tz="Europe/London", name="Date")
        t = mock.Mock()
        t.history.return_value = pd.DataFrame({"Close": [368.0, 369.0]}, index=idx)
        with mock.patch("analyzer.market.yf.Ticker", return_value=t):
            rates, err = market.download_fx_history("EUR")
        self.assertIsNone(err)
        self.assertEqual(t.history.call_args.kwargs["start"], market.FX_HISTORY_START)  # no buy dates sent
        self.assertNotIn("end", t.history.call_args.kwargs)
        self.assertEqual(list(rates["per_usd"]), [368.0])  # today's moving bar not stored


class BamoszWeekendTests(unittest.TestCase):
    def test_saturday_working_day_kept_copy_dropped(self):
        text = "\n".join([
            '"Befektetési jegy devizaneme:";"HUF"',
            '"Dátum";"Árfolyam"',
            '"2026/11/22";"3,30"',  # Sunday copy of Saturday -> dropped
            '"2026/11/21";"3,30"',  # Saturday working day with its own price -> kept
            '"2026/11/20";"3,28"',
        ])
        f = market.parse_bamosz_nav(text.encode("iso-8859-2"))
        self.assertEqual([d.date().isoformat() for d in f["date"]], ["2026-11-20", "2026-11-21"])


class SummarizeTests(unittest.TestCase):
    usd = {"currency": "USD", "quantity": 0, "avg_cost": None, "price_divisor": 1}

    def test_zero_price_lot_is_unknown_not_free(self):
        lots = [{"buy_date": "2025-01-10", "quantity": 1, "unit_price": 0.0, "cost_huf": None}]
        out = summarize_position(self.usd, lots, 150.0, "USD", {"USD": 340.0}, lambda c, d: {"2025-01-10": 400.0}.get(d))
        self.assertIsNone(out["cost_base"])
        self.assertIsNone(out["pnl_base"])
        self.assertIn("without unit price", out["check"])

    def test_average_not_diluted_by_huf_only_lot(self):
        lots = [{"buy_date": "2025-01-10", "quantity": 1, "unit_price": 100.0, "cost_huf": None},
                {"buy_date": "2025-02-10", "quantity": 1, "unit_price": 0.0, "paid_amount": 40000.0, "paid_currency": "HUF"}]
        out = summarize_position(self.usd, lots, 150.0, "USD", {"USD": 340.0}, lambda c, d: {"2025-01-10": 400.0}.get(d))
        self.assertIsNone(out["avg_cost"])                 # not 50
        self.assertAlmostEqual(out["cost_base"], 40000 + 40000)

    def test_quantity_disagreement_flagged(self):
        item = dict(self.usd, quantity=7)
        lots = [{"buy_date": "2025-01-10", "quantity": 10, "unit_price": 100.0, "paid_amount": 400000.0, "paid_currency": "HUF"}]
        out = summarize_position(item, lots, 150.0, "USD", {"USD": 340.0}, lambda c, d: None)
        self.assertEqual(out["quantity"], 10)
        self.assertIn("holding row says 7", out["check"])

    def test_foreign_holding_without_lots_asks_for_purchases(self):
        item = dict(self.usd, quantity=2, avg_cost=100.0)
        out = summarize_position(item, [], 150.0, "USD", {"USD": 340.0}, lambda c, d: None)
        self.assertIsNone(out["pnl_base"])
        self.assertAlmostEqual(out["pnl"], 100.0)
        self.assertIn("Add purchases", out["check"])


class LotsValidationTests(unittest.TestCase):
    def frame(self, **kw):
        row = {"instrument_id": "TKO", "buy_date": "2025-03-04", "quantity": "2", "unit_price": "120.5", "paid_amount": "", "paid_currency": "", "notes": ""}
        row.update(kw)
        return pd.DataFrame([row])

    def test_zero_cost_means_not_given(self):
        self.assertIsNone(validate_lots(self.frame(paid_amount="0"), {"TKO"})[0]["paid_amount"])

    def test_needs_price_or_cost(self):
        with self.assertRaisesRegex(ValueError, "unit_price or paid_amount"):
            validate_lots(self.frame(unit_price="", paid_amount=""), {"TKO"})
        row = validate_lots(self.frame(unit_price="", paid_amount="81000", paid_currency="eur"), {"TKO"})[0]
        self.assertEqual((row["unit_price"], row["paid_amount"], row["paid_currency"]), (0.0, 81000.0, "EUR"))
        legacy = validate_lots(self.frame(unit_price="", cost_huf="5000").drop(columns=["paid_amount", "paid_currency"]), {"TKO"}, "EUR")[0]
        self.assertEqual((legacy["paid_amount"], legacy["paid_currency"]), (5000.0, "HUF"))  # old CSVs keep meaning HUF

    def test_blank_date_and_inf(self):
        with self.assertRaisesRegex(ValueError, "line 2: buy_date is empty"):
            validate_lots(self.frame(buy_date=""), {"TKO"})
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_lots(self.frame(quantity="inf"), {"TKO"})


class ShortWindowHistoryTests(TempDb):
    """1W must not drop a holding whose last price is older than the window (bonds, hand-imported funds)."""

    def test_stale_priced_holding_is_carried_into_one_week(self):
        from analyzer import portfolio
        today = pd.Timestamp("2026-11-24")
        db.upsert_instrument({"instrument_id": "ETF", "name": "ETF", "asset_type": "etf", "quantity": 1, "currency": "HUF"})
        db.upsert_instrument({"instrument_id": "BOND", "name": "Bond", "asset_type": "government_bond", "quantity": 1, "currency": "HUF"})
        days = pd.bdate_range(end=today - pd.Timedelta(days=1), periods=40)
        db.save_prices("ETF", pd.DataFrame({"date": days, "close": 100.0, "currency": "HUF"}), "test")
        db.save_prices("BOND", pd.DataFrame({"date": [today - pd.Timedelta(days=25)], "close": [10000.0],
                                             "currency": ["HUF"]}), "test")
        with mock.patch.object(portfolio, "_today", return_value=today):
            week = portfolio.value_history(8, "HUF")
            month = portfolio.value_history(31, "HUF")
        self.assertEqual(sorted(week["included"]), ["BOND", "ETF"])
        self.assertEqual(week["points"][-1], month["points"][-1])
        self.assertEqual(week["points"][-1]["value"], 10100.0)
        self.assertGreaterEqual(week["points"][0]["date"], (today - pd.Timedelta(days=8)).date().isoformat())

    def test_window_without_any_price_inside_still_reaches_today(self):
        from analyzer import portfolio
        today = pd.Timestamp("2026-11-24")
        for iid, days_ago, close in (("FUND", 12, 20.0), ("BOND2", 20, 1000.0)):
            db.upsert_instrument({"instrument_id": iid, "name": iid, "asset_type": "mutual_fund", "quantity": 1, "currency": "HUF"})
            db.save_prices(iid, pd.DataFrame({"date": [today - pd.Timedelta(days=days_ago)], "close": [close],
                                              "currency": ["HUF"]}), "test")
        with mock.patch.object(portfolio, "_today", return_value=today):
            week = portfolio.value_history(8, "HUF")
            month = portfolio.value_history(31, "HUF")
        self.assertTrue(week["points"])
        self.assertEqual(week["points"][-1], {"date": "2026-11-24", "value": 1020.0})
        self.assertEqual(month["points"][-1], week["points"][-1])

    def test_purchase_after_the_last_price_counts_from_its_date(self):
        from analyzer import portfolio
        today = pd.Timestamp("2026-11-24")
        db.upsert_instrument({"instrument_id": "BOND3", "name": "Bond", "asset_type": "government_bond", "quantity": 0, "currency": "HUF"})
        db.save_prices("BOND3", pd.DataFrame({"date": [today - pd.Timedelta(days=20)], "close": [1000.0],
                                              "currency": ["HUF"]}), "test")
        db.add_lots([{"instrument_id": "BOND3", "buy_date": (today - pd.Timedelta(days=3)).date().isoformat(),
                      "quantity": 2, "unit_price": 1000.0}])
        with mock.patch.object(portfolio, "_today", return_value=today):
            week = portfolio.value_history(8, "HUF")
        values = {p["date"]: p["value"] for p in week["points"]}
        self.assertEqual(values["2026-11-24"], 2000.0)
        self.assertEqual(values["2026-11-21"], 2000.0)   # the purchase date
        self.assertNotIn("2026-11-20", {d for d, v in values.items() if v})  # nothing held before it


class MinorUnitTests(unittest.TestCase):
    def test_pence_quote_matches_a_gbp_holding_with_divisor_100(self):
        from analyzer.engine import currency_mismatch
        self.assertFalse(currency_mismatch("GBP", "GBp", 100))
        self.assertFalse(currency_mismatch("ILS", "ILA", 100.0))
        self.assertTrue(currency_mismatch("GBP", "GBp", 1))       # pence read as pounds
        self.assertTrue(currency_mismatch("GBp", "GBp", 1))       # a minor unit is not a holding currency
        self.assertTrue(currency_mismatch("ZAC", "ZAc", 100))     # "ZAC" is not ZAR
        self.assertFalse(currency_mismatch("EUR", "eur"))
        self.assertTrue(currency_mismatch("EUR", "USD"))


class HistoryEdgeTests(TempDb):
    def test_currency_from_full_history_and_nothing_after_today(self):
        from analyzer import portfolio
        today = pd.Timestamp("2026-11-24")
        db.upsert_instrument({"instrument_id": "LSE", "name": "London", "asset_type": "stock", "quantity": 1, "currency": "GBP"})
        db.upsert_instrument({"instrument_id": "OK", "name": "Ok", "asset_type": "stock", "quantity": 1, "currency": "HUF"})
        old = pd.bdate_range(end=today - pd.Timedelta(days=40), periods=5)
        db.save_prices("LSE", pd.DataFrame({"date": old, "close": 7500.0, "currency": "GBp"}), "yahoo")
        db.save_prices("LSE", pd.DataFrame({"date": [today - pd.Timedelta(days=2)], "close": [125.0]}), "csv")  # no currency
        days = pd.bdate_range(end=today, periods=30)
        db.save_prices("OK", pd.DataFrame({"date": days, "close": 100.0, "currency": "HUF"}), "test")
        db.save_prices("OK", pd.DataFrame({"date": [today + pd.Timedelta(days=1)], "close": [999.0], "currency": ["HUF"]}), "test")
        with mock.patch.object(portfolio, "_today", return_value=today):
            ends = {d: portfolio.value_history(d, "HUF")["points"][-1] for d in (8, 31, 92, None)}
        self.assertEqual(len({(p["date"], p["value"]) for p in ends.values()}), 1)  # same right edge in every window
        self.assertEqual(ends[8], {"date": "2026-11-24", "value": 100.0})          # LSE (pence) excluded, no future bar


class LotPlatformMigrationTests(TempDb):
    def test_old_lots_table_gains_platform_and_keeps_rows(self):
        def run(sql):  # closes the handle: an open one locks the file on Windows
            conn = sqlite3.connect(db.DB_PATH)
            try:
                conn.execute(sql)
                conn.commit()
            finally:
                conn.close()
        run("ALTER TABLE lots DROP COLUMN platform")  # simulate a database from before the platform column
        db.upsert_instrument({"instrument_id": "A", "name": "A", "asset_type": "stock", "quantity": 0, "currency": "EUR"})
        run("INSERT INTO lots(instrument_id, buy_date, quantity, unit_price) VALUES ('A','2025-01-02',1,10)")
        db.initialize()
        db.initialize()  # idempotent
        lots = db.get_lots()
        self.assertEqual(len(lots), 1)
        self.assertTrue(pd.isna(lots.iloc[0]["platform"]))
        db.add_lots([{"instrument_id": "A", "buy_date": "2025-02-03", "quantity": 1, "unit_price": 11, "platform": "OTP"}])
        self.assertEqual(db.get_platforms(), ["OTP"])

    def test_csv_purchase_limits_match_the_api(self):
        from analyzer.validation import validate_lots
        frame = pd.DataFrame([{"instrument_id": "A", "buy_date": "2025-01-02", "quantity": "1", "unit_price": "10", "notes": "n" * 501}])
        with self.assertRaises(ValueError):
            validate_lots(frame, {"A"})
        frame = pd.DataFrame([{"instrument_id": "A", "buy_date": "2025-01-02", "quantity": "1", "unit_price": "10", "platform": "p" * 61}])
        with self.assertRaises(ValueError):
            validate_lots(frame, {"A"})
        ok = validate_lots(pd.DataFrame([{"instrument_id": "A", "buy_date": "2025-01-02", "quantity": "1", "unit_price": "10",
                                          "notes": "n" * 500, "platform": "Revolut"}]), {"A"})
        self.assertEqual((len(ok[0]["notes"]), ok[0]["platform"]), (500, "Revolut"))


class AddLotsTests(TempDb):
    def test_atomic_and_duplicates_skipped(self):
        db.upsert_instrument({"instrument_id": "TKO", "name": "TKO", "asset_type": "stock", "currency": "USD"})
        lot = {"instrument_id": "TKO", "buy_date": "2025-03-04", "quantity": 2, "unit_price": 120.5, "paid_amount": None}
        self.assertEqual(db.add_lots([lot]), (1, 0))
        self.assertEqual(db.add_lots([lot, dict(lot, quantity=3)]), (1, 1))
        with self.assertRaises(sqlite3.IntegrityError):  # unknown instrument -> whole batch rejected
            db.add_lots([dict(lot, quantity=9), dict(lot, instrument_id="NOPE")])
        self.assertEqual(db.count_lots("TKO"), 2)


class LotPaymentMigrationTests(unittest.TestCase):
    def test_cost_huf_becomes_paid_amount_in_huf(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3"):
                db.initialize()
                conn = sqlite3.connect(db.DB_PATH)
                conn.executescript(
                    "DROP TABLE lots; CREATE TABLE lots (lot_id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id TEXT NOT NULL,"
                    " buy_date TEXT NOT NULL, quantity REAL NOT NULL, unit_price REAL NOT NULL, cost_huf REAL, notes TEXT);"
                    "INSERT INTO instruments(instrument_id, name, asset_type, currency) VALUES ('A','A','stock','EUR');"
                    "INSERT INTO lots(instrument_id, buy_date, quantity, unit_price, cost_huf) VALUES ('A','2025-01-02',1,10,4000),"
                    "('A','2025-02-03',1,11,NULL);")
                conn.close()
                db.initialize()
                lots = db.get_lots("A").sort_values("buy_date").to_dict("records")
                self.assertEqual((lots[0]["paid_amount"], lots[0]["paid_currency"]), (4000.0, "HUF"))
                self.assertTrue(pd.isna(lots[1]["paid_currency"]))
                db.initialize()  # idempotent


class RateBookTests(unittest.TestCase):
    def test_any_pair_through_usd_with_staleness_limit(self):
        from analyzer.portfolio import RateBook
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3"):
                db.initialize()
                db.save_fx_usd("HUF", pd.DataFrame({"date": ["2025-01-02"], "per_usd": [340.0]}), "t")
                db.save_fx_usd("EUR", pd.DataFrame({"date": ["2025-01-02"], "per_usd": [0.85]}), "t")
                book = RateBook("HUF")
                self.assertAlmostEqual(book.rate("EUR", "2025-01-04"), 400.0)   # weekend -> Friday
                self.assertAlmostEqual(book.rate("USD", "2025-01-02"), 340.0)
                self.assertEqual(book.rate("HUF", "2020-01-01"), 1.0)
                self.assertIsNone(book.rate("EUR", "2025-02-01"))              # > 7 days stale
                self.assertAlmostEqual(RateBook("EUR").rate("HUF", "2025-01-02"), 0.85 / 340.0)


class MigrationFailureTests(unittest.TestCase):
    def test_failed_migration_leaves_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3"):
                conn = sqlite3.connect(db.DB_PATH)
                conn.executescript("CREATE TABLE instruments (instrument_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                                   "asset_type TEXT NOT NULL CHECK(asset_type IN ('stock')));")
                conn.close()
                with self.assertRaises(sqlite3.OperationalError):  # old table lacks columns the copy needs
                    db.initialize()
                self.assertEqual(list(d.glob("*.before-migration-*")), [])


if __name__ == "__main__":
    unittest.main()
