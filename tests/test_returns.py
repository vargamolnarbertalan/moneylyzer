"""Money-weighted return (XIRR): the solver, the under-a-year period return, and the cash flows behind it."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd

from analyzer import db, engine, portfolio
from analyzer.returns import money_weighted


class MoneyWeightedTests(unittest.TestCase):
    def test_a_year_or_more_gives_the_annual_rate(self):
        out = money_weighted([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)], date(2026, 1, 1))
        self.assertAlmostEqual(out["rate"], 0.10, places=6)
        self.assertTrue(out["annualised"])
        self.assertEqual(out["since"], "2025-01-01")

    def test_several_flows_solve_the_equation(self):
        flows = [(date(2024, 1, 1), -1000), (date(2024, 7, 1), -1000), (date(2025, 3, 1), 300), (date(2026, 1, 1), 2000)]
        r = money_weighted(flows, date(2026, 1, 1))["rate"]
        npv = sum(cf / (1 + r) ** ((d - date(2024, 1, 1)).days / 365) for d, cf in flows)
        self.assertAlmostEqual(npv, 0.0, places=6)

    def test_under_a_year_the_period_return_is_not_annualised(self):
        out = money_weighted([(date(2026, 11, 12), -1000), (date(2026, 11, 26), 1030)], date(2026, 11, 26))
        self.assertAlmostEqual(out["rate"], 0.03, places=6)  # +3% in two weeks, not +116% a year
        self.assertFalse(out["annualised"])

    def test_edge_cases(self):
        self.assertIsNone(money_weighted([(date(2026, 1, 1), -1000)], date(2026, 2, 1)))  # nothing came back yet
        same_day = money_weighted([(date(2026, 11, 26), -1000), (date(2026, 11, 26), 990)], date(2026, 11, 26))
        self.assertAlmostEqual(same_day["rate"], -0.01)  # bought today: the plain ratio
        loss = money_weighted([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 10)], date(2026, 1, 1))
        self.assertAlmostEqual(loss["rate"], -0.99, places=6)


class ReviewFixTests(unittest.TestCase):
    """Adversarial review of XIRR (2026-11-26)."""

    def test_a_very_old_first_buy_does_not_overflow(self):
        out = money_weighted([(date(1925, 3, 1), -1000), (date(2026, 11, 26), 1100)], date(2026, 11, 26))
        self.assertTrue(out["annualised"])
        self.assertAlmostEqual(out["rate"], 1.1 ** (365 / (date(2026, 11, 26) - date(1925, 3, 1)).days) - 1, places=9)

    def test_a_sold_out_holding_keeps_its_return(self):
        flows = [(date(2026, 1, 1), -1000), (date(2026, 2, 1), 1100)]
        for today in (date(2026, 2, 1), date(2026, 8, 1), date(2027, 6, 1)):
            out = money_weighted(flows, today)
            self.assertAlmostEqual(out["rate"], 0.10, places=6)  # not more every day while nothing is invested
            self.assertFalse(out["annualised"])
        self.assertFalse(out["ongoing"])
        self.assertEqual(out["until"], "2026-02-01")

    def test_one_small_old_buy_does_not_make_it_annual(self):
        flows = [(date(2024, 11, 1), -100), (date(2026, 11, 19), -1_000_000), (date(2026, 11, 26), 1_050_105)]
        out = money_weighted(flows, date(2026, 11, 26))
        self.assertFalse(out["annualised"])  # the money was in for a week on average, not two years
        self.assertAlmostEqual(out["rate"], 0.05, places=3)

    def test_several_possible_rates_give_no_figure(self):
        flows = [(date(2021, 1, 6), -3586), (date(2021, 4, 19), -3782), (date(2021, 6, 1), -2192), (date(2022, 3, 11), 8770),
                 (date(2022, 4, 4), -577), (date(2022, 12, 15), 832), (date(2026, 10, 27), -3939), (date(2026, 11, 26), 3494)]
        self.assertIsNone(money_weighted(flows, date(2026, 11, 26)))


class CashFlowTests(unittest.TestCase):
    def rate(self, ccy, day):
        return {"USD": 350.0}.get(ccy)

    def test_buys_sales_and_todays_value_in_the_base_currency(self):
        item = {"currency": "USD", "price_divisor": 1}
        lots = [{"buy_date": "2025-01-02", "side": "buy", "quantity": 2, "unit_price": 100},
                {"buy_date": "2025-06-02", "side": "sell", "quantity": 1, "unit_price": 120}]
        flows = engine.cash_flows(item, lots, 40_000.0, False, "2026-11-26", self.rate, "HUF")
        self.assertEqual(flows, [("2025-01-02", -70_000.0), ("2025-06-02", 42_000.0), ("2026-11-26", 40_000.0)])

    def test_unknown_amounts_and_no_transactions_give_nothing(self):
        item = {"currency": "EUR", "price_divisor": 1}
        lots = [{"buy_date": "2025-01-02", "side": "buy", "quantity": 2, "unit_price": 100}]
        self.assertIsNone(engine.cash_flows(item, lots, 1.0, False, "2026-11-26", self.rate, "HUF"))  # no EUR rate
        self.assertIsNone(engine.cash_flows(item, [], 1.0, False, "2026-11-26", self.rate, "HUF"))
        self.assertIsNone(engine.cash_flows({"currency": "USD"}, [{**lots[0], "unit_price": None}], 1.0, False, "2026-11-26", self.rate, "HUF"))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3"),
                  mock.patch("analyzer.portfolio._today", return_value=pd.Timestamp("2026-01-01"))):
            p.start()
            self.addCleanup(p.stop)
        db.initialize()

    def test_rows_and_totals_carry_the_return(self):
        for iid, qty_row in (("A", 0), ("B", 3)):
            db.upsert_instrument({"instrument_id": iid, "name": iid, "asset_type": "stock", "quantity": qty_row,
                                  "currency": "HUF", "market_symbol": None, "price_divisor": 1})
            db.save_prices(iid, pd.DataFrame({"date": ["2025-12-31"], "close": [110.0], "currency": ["HUF"]}), "t")
        db.add_lots([{"instrument_id": "A", "buy_date": "2025-01-01", "quantity": 10, "unit_price": 100}])
        snap = portfolio.build_snapshot({}, {}, "HUF")
        a = next(r for r in snap["rows"] if r["id"] == "A")
        b = next(r for r in snap["rows"] if r["id"] == "B")
        self.assertAlmostEqual(a["mwr"]["rate"], 0.10, places=6)  # 1000 HUF in, worth 1100 a year later
        self.assertIsNone(b["mwr"])  # no transactions: nothing to date the money by
        self.assertAlmostEqual(snap["totals"]["mwr"]["rate"], 0.10, places=6)
        self.assertEqual((snap["totals"]["mwr_holdings"], snap["totals"]["mwr_left_out"]), (1, 0))

    def test_sold_out_and_ancient_holdings(self):
        for iid in ("SOLD", "OLD"):
            db.upsert_instrument({"instrument_id": iid, "name": iid, "asset_type": "stock", "quantity": 0,
                                  "currency": "HUF", "market_symbol": None, "price_divisor": 1})
            db.save_prices(iid, pd.DataFrame({"date": ["2025-12-31"], "close": [110.0], "currency": ["HUF"]}), "t")
        db.add_lots([{"instrument_id": "SOLD", "buy_date": "2025-03-01", "quantity": 10, "unit_price": 100},
                     {"instrument_id": "SOLD", "buy_date": "2025-04-01", "side": "sell", "quantity": 10, "unit_price": 110},
                     {"instrument_id": "OLD", "buy_date": "1925-03-01", "quantity": 1, "unit_price": 1}])  # a typo year
        snap = portfolio.build_snapshot({}, {}, "HUF")  # must not raise (it once overflowed)
        sold = next(r for r in snap["rows"] if r["id"] == "SOLD")["mwr"]
        self.assertAlmostEqual(sold["rate"], 0.10, places=6)  # the period it was held, not stretched to today
        self.assertEqual((sold["ongoing"], sold["until"]), (False, "2025-04-01"))
        self.assertTrue(next(r for r in snap["rows"] if r["id"] == "OLD")["mwr"]["annualised"])


if __name__ == "__main__":
    unittest.main()
