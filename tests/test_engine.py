import unittest

import numpy as np
import pandas as pd

from analyzer.engine import analyze_history, bootstrap_scenarios, currency_value


class EngineTests(unittest.TestCase):
    def test_government_bond_is_not_technical_buy_sell(self):
        dates = pd.date_range("2024-01-01", periods=260, freq="B")
        history = pd.DataFrame({"date": dates, "close": np.linspace(98, 105, len(dates))})
        metrics = analyze_history({"asset_type": "government_bond"}, history)
        self.assertEqual(metrics["signal"], "NOT RATED")

    def test_insufficient_history_is_not_scored(self):
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        history = pd.DataFrame({"date": dates, "close": np.linspace(50, 60, len(dates))})
        metrics = analyze_history({"asset_type": "stock"}, history)
        self.assertEqual(metrics["signal"], "INSUFFICIENT DATA")
        self.assertIsNone(metrics["score"])

    def test_scenarios_are_ordered(self):
        rng = np.random.default_rng(1)
        returns = rng.normal(0.0003, 0.01, 400)
        prices = 100 * np.exp(np.cumsum(returns))
        history = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=400, freq="B"), "close": prices})
        scenarios = bootstrap_scenarios("TEST", history)
        self.assertEqual(len(scenarios), 3)
        for row in scenarios:
            self.assertLessEqual(row["p10"], row["median"])
            self.assertLessEqual(row["median"], row["p90"])


    def test_clear_uptrend_gets_buy_candidate_not_instruction(self):
        dates = pd.date_range("2024-01-01", periods=260, freq="B")
        prices = np.linspace(20, 60, len(dates))
        history = pd.DataFrame({"date": dates, "close": prices})
        metrics = analyze_history({"asset_type": "stock"}, history)
        self.assertEqual(metrics["signal"], "BUY CANDIDATE")
        self.assertEqual(metrics["score"], 4)

    def test_bond_price_divisor(self):
        self.assertAlmostEqual(currency_value(1_000_000, 98.5, 100), 985_000)


if __name__ == "__main__":
    unittest.main()
