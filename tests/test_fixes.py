import json
import os
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from analyzer import ai, market
from analyzer.engine import currency_mismatch, latest_price_currency, summarize_position
from analyzer.market import normalize_history
from analyzer.validation import validate_holdings


class YahooDateTests(unittest.TestCase):
    def test_european_close_keeps_exchange_trading_date(self):
        idx = pd.DatetimeIndex(["2026-11-20", "2026-11-23"], tz="Europe/Berlin", name="Date")
        frame = normalize_history(pd.DataFrame({"Close": [100.0, 101.0]}, index=idx))
        self.assertEqual([d.date().isoformat() for d in frame["date"]], ["2026-11-20", "2026-11-23"])

    def test_naive_dates_are_unchanged(self):
        idx = pd.DatetimeIndex(["2026-11-20"], name="Date")
        frame = normalize_history(pd.DataFrame({"Close": [5.0]}, index=idx))
        self.assertEqual(frame["date"].iloc[0].date().isoformat(), "2026-11-20")


class HoldingsValidationTests(unittest.TestCase):
    def frame(self, **overrides):
        row = {"instrument_id": "HU0000123456", "name": "Bond", "asset_type": "government_bond", "currency": "HUF"}
        row.update(overrides)
        return pd.DataFrame([row])

    def test_blank_id_is_rejected_not_stored_as_nan(self):
        with self.assertRaises(ValueError):
            validate_holdings(self.frame(instrument_id=float("nan")))

    def test_blank_currency_is_rejected_with_line_number(self):
        with self.assertRaisesRegex(ValueError, "CSV line 2"):
            validate_holdings(self.frame(currency=float("nan")))

    def test_valid_row_defaults(self):
        row = validate_holdings(self.frame())[0]
        self.assertIsNone(row["market_symbol"])
        self.assertEqual(row["price_divisor"], 1.0)
        self.assertEqual(row["notes"], "")

    def test_units_and_cost_are_not_part_of_the_holdings_file(self):
        # owner decision 2026-11-26: they come only from buys and sells (the transactions file)
        for extra in ({"quantity": 10}, {"avg_cost": 2.5}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "no longer takes"):
                validate_holdings(self.frame(**extra))


class YahooCurrencyTests(unittest.TestCase):
    def test_missing_currency_saves_nothing(self):
        idx = pd.DatetimeIndex(["2026-11-20"], tz="Europe/London", name="Date")
        fake = mock.Mock()
        fake.history.return_value = pd.DataFrame({"Close": [9200.0]}, index=idx)
        fake.history_metadata = {}
        with mock.patch("analyzer.market.yf.Ticker", return_value=fake):
            frame, error = market.download_daily_history("VUSA.L")
        self.assertTrue(frame.empty)
        self.assertIn("currency", error)

    def test_currency_taken_from_same_response(self):
        idx = pd.DatetimeIndex(["2026-11-20"], tz="Europe/London", name="Date")
        fake = mock.Mock()
        fake.history.return_value = pd.DataFrame({"Close": [9200.0]}, index=idx)
        fake.history_metadata = {"currency": "GBp"}
        with mock.patch("analyzer.market.yf.Ticker", return_value=fake) as ticker:
            frame, error = market.download_daily_history("VUSA.L")
        self.assertIsNone(error)
        self.assertEqual(frame["currency"].iloc[0], "GBp")
        self.assertEqual(ticker.call_count, 1)


class LiveQuoteTests(unittest.TestCase):
    def quote(self, stamp):
        fake = mock.Mock()
        fake.history_metadata = {"regularMarketPrice": 368.76, "currency": "HUF", "regularMarketTime": stamp}
        with mock.patch("analyzer.market.yf.Ticker", return_value=fake):
            return market.live_quote("EURHUF=X")

    def test_time_as_yfinance_timestamp_or_epoch(self):
        aware = self.quote(pd.Timestamp("2026-11-24 16:05", tz="Europe/Budapest"))
        epoch = self.quote(int(pd.Timestamp("2026-11-24 15:05", tz="UTC").timestamp()))
        for q in (aware, epoch):
            self.assertIsNone(q["error"])
            self.assertEqual(q["price"], 368.76)
            self.assertEqual(q["time"], pd.Timestamp("2026-11-24 15:05", tz="UTC"))

    def test_missing_price_is_an_error(self):
        fake = mock.Mock()
        fake.history_metadata = {}
        with mock.patch("analyzer.market.yf.Ticker", return_value=fake):
            self.assertIsNotNone(market.live_quote("X")["error"])


class FundNavTests(unittest.TestCase):
    def test_fund_symbol(self):
        self.assertEqual(market.fund_isin("fund:hu0000728670"), "HU0000728670")
        self.assertIsNone(market.fund_isin("fund:HU0000728670/../x"))  # never put junk into a URL
        self.assertIsNone(market.fund_isin("AAPL"))

    def test_parse_otp_json(self):
        payload = b'{"currency":"HUF","values":[{"date":"2026-11-19","value":3.37663100},{"date":"2026-11-20","value":3.28957700}]}'
        frame = market.parse_otp_nav(payload)
        self.assertEqual(list(frame["close"]), [3.376631, 3.289577])
        self.assertEqual(set(frame["currency"]), {"HUF"})

    def test_parse_bamosz_csv(self):
        text = "\n".join([
            '"Alap neve:";"OTP Közép-Európai Származtatott Részvény Alap"',
            '"Befektetési jegy devizaneme:";"HUF"',
            '"Dátum";"Árfolyam";"NEÉ";"Kifizetett hozam"',
            '"2026/11/22";"3,289577";"1";""',   # Sunday copy of Friday: dropped
            '"2026/11/20";"3,289577";"38897494809";""',
            '"2026/11/19";"3,376631";"39839087334";""',
        ])
        frame = market.parse_bamosz_nav(text.encode("iso-8859-2"))
        self.assertEqual([d.date().isoformat() for d in frame["date"]], ["2026-11-19", "2026-11-20"])
        self.assertEqual(list(frame["close"]), [3.376631, 3.289577])
        self.assertEqual(set(frame["currency"]), {"HUF"})

    def test_falls_back_to_bamosz(self):
        bamosz = '"Befektetési jegy devizaneme:";"HUF"\n"Dátum";"Árfolyam"\n"2026/11/20";"3,289577"'.encode("iso-8859-2")

        def fake_http(url, data=None):
            if "otpalapkezelo" in url:
                raise OSError("HTTP Error 503")
            return bamosz

        with mock.patch.object(market, "_http", side_effect=fake_http):
            frame, source, error = market.download_fund_nav("HU0000728670", "2026-01-01")
        self.assertIsNone(error)
        self.assertEqual(source, "BAMOSZ")
        self.assertEqual(frame["close"].iloc[-1], 3.289577)


class ValuationTests(unittest.TestCase):
    usd = {"currency": "USD", "quantity": 0, "avg_cost": None, "price_divisor": 1}

    def test_minor_unit_is_not_valued(self):
        item = {"currency": "GBP", "quantity": 100, "avg_cost": 90.0, "price_divisor": 1}
        out = summarize_position(item, [], 9200.0, "GBp", {"GBP": 450.0}, lambda c, d: None)
        self.assertIsNone(out["value"])
        self.assertIn("GBp", out["check"])

    def test_latest_row_without_currency_uses_last_known(self):
        history = pd.DataFrame({"close": [9100.0, 9200.0], "currency": ["GBp", None]})
        self.assertEqual(latest_price_currency(history), "GBp")

    def test_bond_divisor_and_huf_pnl_without_lots(self):
        bond = {"currency": "HUF", "quantity": 1_000_000, "avg_cost": 97.0, "price_divisor": 100}
        out = summarize_position(bond, [], 98.5, "HUF", {}, lambda c, d: None)
        self.assertAlmostEqual(out["value_base"], 985_000)
        self.assertAlmostEqual(out["pnl_base"], 15_000)

    def test_lots_drive_quantity_cost_and_huf_pnl(self):
        lots = [
            {"buy_date": "2025-01-10", "quantity": 10, "unit_price": 100.0, "paid_amount": None},
            {"buy_date": "2025-06-02", "quantity": 5, "unit_price": 130.0, "paid_amount": 250_000.0, "paid_currency": "HUF"},
        ]
        out = summarize_position(self.usd, lots, 150.0, "USD", {"USD": 340.0}, lambda c, d: {"2025-01-10": 400.0}.get(d))
        self.assertEqual(out["quantity"], 15)
        self.assertAlmostEqual(out["avg_cost"], (1000 + 650) / 15)
        self.assertAlmostEqual(out["cost_base"], 10 * 100 * 400 + 250_000)   # buy-date FX, then actual HUF paid
        self.assertAlmostEqual(out["value_base"], 15 * 150 * 340)
        self.assertAlmostEqual(out["pnl_base"], 15 * 150 * 340 - 650_000)
        self.assertAlmostEqual(out["pnl"], 15 * 150 - 1650)                 # in USD: FX effect excluded

    def test_missing_buy_date_rate_means_unknown_not_zero(self):
        lots = [{"buy_date": "2025-01-10", "quantity": 1, "unit_price": 100.0, "paid_amount": None}]
        out = summarize_position(self.usd, lots, 150.0, "USD", {"USD": 340.0}, lambda c, d: None)
        self.assertIsNone(out["cost_base"])
        self.assertIsNone(out["pnl_base"])
        self.assertEqual(out["value_base"], 150 * 340)
        self.assertIn("rate missing", out["check"])

    def test_missing_live_fx_keeps_holding_out_of_huf_total(self):
        item = dict(self.usd, quantity=2)
        out = summarize_position(item, [], 150.0, "USD", {}, lambda c, d: None)
        self.assertEqual(out["value"], 300.0)
        self.assertIsNone(out["value_base"])
        self.assertIn("USD/HUF", out["check"])


class LotsValidationTests(unittest.TestCase):
    def test_valid_and_invalid_rows(self):
        from analyzer.validation import validate_lots
        ok = pd.DataFrame([{"instrument_id": "TKO", "buy_date": "2025-03-04", "quantity": 2, "unit_price": 120.5,
                            "paid_amount": "", "notes": None}])
        row = validate_lots(ok, {"TKO"})[0]
        self.assertEqual((row["buy_date"], row["paid_amount"]), ("2025-03-04", None))
        with self.assertRaisesRegex(ValueError, "unknown instrument_id"):
            validate_lots(ok, {"OTHER"})
        with self.assertRaisesRegex(ValueError, "line 2"):
            validate_lots(ok.assign(quantity=0), {"TKO"})
        with self.assertRaisesRegex(ValueError, "line 2"):
            validate_lots(ok.assign(buy_date="04/03/2025"), {"TKO"})

    def test_holdings_without_header_explains_the_fix(self):
        frame = pd.DataFrame(columns=["OTP-KESZ-B", "OTP fund", "mutual_fund", "1000000", "2.9", "HUF", "x", "1", "y"])
        with self.assertRaisesRegex(ValueError, "first line must be the header row"):
            validate_holdings(frame)


OLD_0_1_0_SCHEMA = """
CREATE TABLE instruments (
    instrument_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK(asset_type IN ('stock','etf','mutual_fund','government_bond')),
    quantity REAL NOT NULL DEFAULT 0,
    avg_cost REAL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    market_symbol TEXT,
    price_divisor REAL NOT NULL DEFAULT 1 CHECK(price_divisor > 0),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE prices (
    instrument_id TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL NOT NULL CHECK(close > 0),
    currency TEXT,
    source TEXT NOT NULL DEFAULT 'CSV import',
    PRIMARY KEY (instrument_id, date),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id) ON DELETE CASCADE
);
INSERT INTO instruments (instrument_id, name, asset_type, quantity, avg_cost, currency, market_symbol, price_divisor, notes)
    VALUES ('FAKE1', 'Fake ETF', 'etf', 10, 5.5, 'EUR', 'FAKE.DE', 1, 'keep me');
INSERT INTO prices VALUES ('FAKE1', '2026-11-20', 6.0, 'EUR', 'CSV import');
INSERT INTO prices VALUES ('FAKE1', '2026-11-23', 6.1, 'EUR', 'CSV import');
CREATE INDEX idx_prices_instrument_date ON prices(instrument_id, date);
"""


class CommodityMigrationTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        import tempfile
        from analyzer import db
        self.db, self.sqlite3 = db, sqlite3
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.patches = [mock.patch.object(db, "DATA_DIR", self.dir),
                        mock.patch.object(db, "DB_PATH", self.dir / "test.sqlite3")]
        for p in self.patches:
            p.start()
        self.run_sql(OLD_0_1_0_SCHEMA)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_sql(self, script):
        conn = self.sqlite3.connect(self.db.DB_PATH)
        conn.executescript(script)
        conn.close()

    def query(self, path, sql):
        conn = self.sqlite3.connect(path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def backups(self):
        return sorted(self.dir.glob("test.before-migration-*.sqlite3"))

    def test_old_database_accepts_commodity_and_keeps_everything(self):
        db = self.db
        original = db.DB_PATH.read_bytes()
        rows_before = self.query(db.DB_PATH, "SELECT * FROM instruments ORDER BY instrument_id")
        db.initialize()
        (backup,) = self.backups()
        self.assertEqual(backup.read_bytes(), original)                      # byte-identical backup
        self.assertEqual(self.query(backup, "PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.query(db.DB_PATH, "SELECT * FROM instruments ORDER BY instrument_id"), rows_before)
        self.assertEqual(self.query(db.DB_PATH, "SELECT count(*) FROM prices"), [(2,)])
        # The old secondary index duplicated PRIMARY KEY (instrument_id, date) and is dropped;
        # per-instrument price lookups must still be served by the primary-key index.
        self.assertFalse(self.query(db.DB_PATH, "SELECT 1 FROM sqlite_master WHERE name='idx_prices_instrument_date'"))
        plan = " ".join(r[-1] for r in self.query(
            db.DB_PATH, "EXPLAIN QUERY PLAN SELECT date FROM prices WHERE instrument_id='FAKE1' AND date >= '2020-01-01'"))
        self.assertIn("sqlite_autoindex_prices_1", plan)
        self.assertEqual(self.query(db.DB_PATH, "PRAGMA integrity_check"), [("ok",)])

        migrated = db.DB_PATH.read_bytes()
        db.initialize()                                                         # second start: no-op
        self.assertEqual(db.DB_PATH.read_bytes(), migrated)
        self.assertEqual(len(self.backups()), 1)

        db.upsert_instrument({"instrument_id": "XAU", "name": "Gold", "asset_type": "commodity",
                              "quantity": 0.25, "currency": "USD", "market_symbol": "GC=F"})
        db.delete_instrument("FAKE1")  # foreign key + cascade still work after the rebuild
        self.assertTrue(db.get_prices("FAKE1").empty)

    def test_unknown_type_still_rejected(self):
        self.db.initialize()
        with self.assertRaises(self.sqlite3.IntegrityError):
            self.db.upsert_instrument({"instrument_id": "X", "name": "X", "asset_type": "crypto", "currency": "USD"})

    def test_preexisting_orphan_price_does_not_block_start(self):
        self.run_sql("PRAGMA foreign_keys=OFF; INSERT INTO prices VALUES ('GHOST', '2026-01-02', 1.0, 'EUR', 'x');")
        self.db.initialize()
        self.assertTrue(any("'commodity'" in r[0] for r in self.query(self.db.DB_PATH, "SELECT sql FROM sqlite_master WHERE name='instruments'")))

    def test_stale_temp_table_is_replaced(self):
        self.run_sql(OLD_0_1_0_SCHEMA.split("CREATE TABLE prices")[0].replace("CREATE TABLE instruments", "CREATE TABLE instruments_new"))
        self.db.initialize()
        self.db.upsert_instrument({"instrument_id": "XAU", "name": "Gold", "asset_type": "commodity", "currency": "USD"})
        self.assertFalse(self.query(self.db.DB_PATH, "SELECT 1 FROM sqlite_master WHERE name='instruments_new'"))

    def test_concurrent_starts_migrate_once(self):
        import threading
        barrier, errors = threading.Barrier(4), []

        def start():
            barrier.wait()
            try:
                self.db.initialize()
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.query(self.db.DB_PATH, "SELECT count(*) FROM prices"), [(2,)])


class CommodityRulesTests(unittest.TestCase):
    def test_commodity_csv_row_and_signal(self):
        row = validate_holdings(pd.DataFrame([{"instrument_id": "XAU", "name": "Gold", "asset_type": "commodity",
                                               "currency": "usd", "market_symbol": "GC=F"}]))[0]
        self.assertEqual((row["asset_type"], row["currency"]), ("commodity", "USD"))
        from analyzer.engine import analyze_history
        dates = pd.date_range("2025-01-01", periods=260, freq="B")
        metrics = analyze_history({"asset_type": "commodity"}, pd.DataFrame({"date": dates, "close": range(1, 261)}))
        self.assertNotEqual(metrics["signal"], "NOT RATED")


class CurrencyMismatchTests(unittest.TestCase):
    def test_cases(self):
        self.assertFalse(currency_mismatch("EUR", "EUR"))
        self.assertFalse(currency_mismatch("EUR", "eur"))
        self.assertFalse(currency_mismatch("EUR", None))
        self.assertFalse(currency_mismatch("EUR", float("nan")))
        self.assertTrue(currency_mismatch("EUR", "USD"))
        self.assertTrue(currency_mismatch("GBP", "GBp"))  # London quotes in pence: 100x off


class ClaudeCliTests(unittest.TestCase):
    def test_command_is_text_only_and_isolated(self):
        cmd = ai.build_command("claude", "opus")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        for flag in ("-p", "--safe-mode", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, cmd)

    def test_api_key_is_not_passed_to_cli(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertNotIn("ANTHROPIC_API_KEY", ai.cli_environment())

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            ai.explain_metrics("some-other-model", {}, None, {}, [])

    def test_prompt_sends_no_quantities_and_escapes_at_sign(self):
        instrument = {"name": "Fund @C:/secret.csv", "asset_type": "etf", "currency": "GBP",
                      "quantity": 123456, "avg_cost": 98.7}
        metrics = {"signal": "WATCH", "latest_price": 9200.0, "future_secret": "x"}
        prompt = ai.build_prompt(instrument, "GBp", metrics, [])
        self.assertNotIn("@", prompt)
        self.assertNotIn("123456", prompt)
        self.assertNotIn("98.7", prompt)
        self.assertNotIn("future_secret", prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(payload["instrument"]["name"], "Fund @C:/secret.csv")
        self.assertEqual(payload["instrument"]["price_currency"], "GBp")

    def test_billing_variables_are_removed(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_BASE_URL": "http://x"}):
            env = ai.cli_environment()
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_parse_message_list_and_junk(self):
        listed = json.dumps([{"type": "system"}, {"type": "result", "result": "ok", "is_error": False}])
        self.assertEqual(ai.parse_cli_output(0, listed, ""), "ok")
        with self.assertRaises(RuntimeError):
            ai.parse_cli_output(0, "null", "")

    def test_parse_success_and_errors(self):
        self.assertEqual(ai.parse_cli_output(0, json.dumps({"result": "hi", "is_error": False}), ""), "hi")
        with self.assertRaises(RuntimeError):
            ai.parse_cli_output(1, json.dumps({"result": "Not logged in", "is_error": True}), "")
        with self.assertRaises(RuntimeError):
            ai.parse_cli_output(1, "", "boom")


if __name__ == "__main__":
    unittest.main()
