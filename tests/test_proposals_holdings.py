"""AI change cards for holdings and transactions (phase 3): an import from a screenshot is a set of these cards."""
import json
import threading
import time
import unittest
from unittest import mock

import pandas as pd

from analyzer import proposals
from analyzer.persona import normalize_preferences
from analyzer.symbols import SymbolError

QUOTES = {"VUSA.L": {"price": 9000.0, "currency": "GBP", "quote_currency": "GBp", "divisor": 100.0},
          "VWCE.DE": {"price": 120.0, "currency": "EUR", "quote_currency": "EUR", "divisor": 1.0}}


def fake_quote(symbol):
    if symbol.upper() == "DOWN.DE":
        raise SymbolError("Quote failed: timed out", 503)
    if symbol.upper() not in QUOTES:
        raise SymbolError("No price.", 404)
    return QUOTES[symbol.upper()]


def state(**over):
    holdings = {
        "AAPL": {"name": "Apple", "type": "stock", "currency": "USD", "symbol": "AAPL", "quantity": 0.0, "avg_cost": None,
                 "notes": "", "price_divisor": 1.0, "units": 10.0,
                 "lots": [{"lot_id": 7, "date": "2024-01-02", "side": "buy", "quantity": 10.0, "unit_price": 180.0}]},
        "OTP": {"name": "OTP Bank", "type": "stock", "currency": "HUF", "symbol": "OTP.BD", "quantity": 5.0, "avg_cost": 20000.0,
                "notes": "Long term.", "price_divisor": 1.0, "units": 5.0, "lots": []},
    }
    base = {"rules": [], "preferences": normalize_preferences({}), "base_currency": "HUF", "holdings": holdings,
            "quote": fake_quote}
    return {**base, **over}


def block(obj) -> str:
    return "```moneylyzer-proposal\n" + json.dumps(obj) + "\n```"


def one(obj, st=None):
    _, cards, notes = proposals.parse(block({**obj, "reason": obj.get("reason", "From your screenshot.")}), st or state(),
                                      proposals.MAX_IMPORT_PROPOSALS)
    return cards[0] if cards else None, notes


class HoldingAddTests(unittest.TestCase):
    def test_a_new_holding_takes_currency_and_divisor_from_its_symbol(self):
        card, notes = one({"type": "holding.add", "name": "Vanguard S&P 500", "asset_type": "etf", "symbol": "VUSA.L",
                           "quantity": 12, "unit_price": 7512.5, "date": "2025-03-03", "platform": "Revolut"})
        self.assertEqual(notes, [])
        # units and cost only through its first buy, stored with it (owner decision 2026-11-26)
        self.assertEqual(card["apply"], {"instrument_id": "VUSA", "name": "Vanguard S&P 500", "asset_type": "etf",
                                         "currency": "GBP", "market_symbol": "VUSA.L", "price_divisor": 100.0, "notes": None,
                                         "buy": {"buy_date": "2025-03-03", "quantity": 12.0, "unit_price": 7512.5,
                                                 "paid_amount": None, "paid_currency": None, "platform": "Revolut"}})
        rows = {r["label"]: r["after"] for r in card["changes"]}
        self.assertEqual(rows["Currency"], "GBP, prices in GBp (100 = 1 GBP)")
        self.assertEqual(rows["Price per unit"], "7 512,5 GBp (100 = 1 GBP)")
        self.assertEqual(card["summary"], "Add the holding Vanguard S&P 500 with a buy of 12")
        self.assertNotIn("confirm", card)  # 7 512,5 pence against a price of 9 000 pence: plausible
        self.assertNotIn("needs_date", card)

    def test_a_buy_without_a_date_leaves_it_to_the_user(self):
        # a Revolut confirmation shows "január 2." without a year: the card asks for the date instead of guessing
        obj = {"type": "holding.add", "name": "iShares Semiconductors", "asset_type": "etf", "symbol": "VWCE.DE",
               "quantity": 395.45731089, "unit_price": 9.86, "paid_amount": 3900}
        card, _ = one(obj)
        self.assertTrue(card["needs_date"])
        self.assertEqual(card["changes"][4], {"label": "Date", "before": None, "after": "pick the date below", "date": True})
        self.assertIsNone(card["apply"]["buy"]["buy_date"])
        picked = proposals.preview("holding.add", {**card["payload"], "date": "2026-01-02"}, state())
        self.assertEqual((picked["apply"]["buy"]["buy_date"], picked["apply"]["buy"]["paid_currency"]), ("2026-01-02", "EUR"))
        self.assertEqual(proposals.preview("holding.add", {**card["payload"], "date": "2999-01-01"}, state())["ok"], False)

    def test_a_holding_without_units_has_no_buy(self):
        card, _ = one({"type": "holding.add", "name": "Watch", "asset_type": "stock", "currency": "USD"})
        self.assertNotIn("buy", card["apply"])
        card, notes = one({"type": "holding.add", "name": "Watch", "asset_type": "stock", "currency": "USD", "unit_price": 5})
        self.assertIn("without the number of units", notes[0])

    def test_a_cost_far_from_the_price_asks_first(self):
        # 12 000 for an ETF trading at 120: forints, or pence, typed as euros; the card says so and asks
        card, _ = one({"type": "holding.add", "name": "Vanguard All-World", "asset_type": "etf", "symbol": "VWCE.DE",
                       "quantity": 3, "unit_price": 12000})
        check = next(r for r in card["changes"] if r["label"] == "Check")
        self.assertIn("over 30 times off", check["after"])
        self.assertNotIn("120", check["after"])  # no live price in the text: the card must re-check as the same
        self.assertIn("Apply it?", card["confirm"])
        card, _ = one({"type": "holding.add", "name": "Vanguard All-World", "asset_type": "etf", "symbol": "VWCE.DE",
                       "quantity": 3, "unit_price": 110})
        self.assertNotIn("confirm", card)

    def test_a_price_unit_is_not_a_currency(self):
        for code in ("GBp", "gbx", "ZAc"):
            with self.subTest(code=code):
                card, notes = one({"type": "holding.add", "name": "Private fund", "asset_type": "mutual_fund", "currency": code})
                self.assertIsNone(card)
                self.assertIn("is a price unit", notes[0])
        card, _ = one({"type": "holding.add", "name": "Private fund", "asset_type": "mutual_fund", "currency": "gbp"})
        self.assertEqual(card["apply"]["currency"], "GBP")

    def test_a_symbol_that_couldnt_be_checked_says_so(self):
        card, notes = one({"type": "holding.add", "name": "Down", "asset_type": "etf", "symbol": "DOWN.DE"})
        self.assertIsNone(card)
        self.assertIn("couldn't be checked right now", notes[0])

    def test_a_name_is_short(self):
        card, notes = one({"type": "holding.add", "name": "A" * 81, "asset_type": "stock", "currency": "USD"})
        self.assertIsNone(card)
        self.assertIn("longer than 80", notes[0])

    def test_what_a_new_holding_cannot_be(self):
        for obj, why in (
            ({"name": "Apple again", "asset_type": "stock", "symbol": "aapl"}, "already have a holding with that symbol"),
            ({"name": "Mystery", "asset_type": "stock", "symbol": "NOPE.XX"}, "no price"),
            ({"name": "otp bank", "asset_type": "stock", "currency": "HUF"}, "already have a holding with that name"),
            ({"name": "Private fund", "asset_type": "mutual_fund"}, "needs its currency"),
            ({"name": "Thing", "asset_type": "crypto", "currency": "USD"}, "type Moneylyzer doesn't have"),
            ({"name": "Thing", "asset_type": "stock", "currency": "USD", "quantity": -1, "unit_price": 1}, "more than 0"),
            ({"name": "Thing", "asset_type": "stock", "currency": "USD", "quantity": 1}, "neither a price"),
        ):
            with self.subTest(why=why):
                card, notes = one({"type": "holding.add", **obj})
                self.assertIsNone(card)
                self.assertIn(why, notes[0])

    def test_its_id_never_takes_an_existing_one(self):
        card, _ = one({"type": "holding.add", "name": "Otp", "asset_type": "stock", "currency": "HUF"})
        self.assertEqual(card["apply"]["instrument_id"], "OTP-2")


class HoldingUpdateTests(unittest.TestCase):
    def test_units_and_cost_never_change_on_their_own(self):
        for changes in ({"quantity": 8}, {"avg_cost": 21000}):
            with self.subTest(changes=changes):
                card, notes = one({"type": "holding.update", "holding": "OTP", "changes": changes})
                self.assertIsNone(card)
                self.assertIn("only from buys and sells", notes[0])

    def test_what_loses_or_overwrites_something_asks_first(self):
        for changes, careful in (({"name": "OTP Nyrt."}, "renames"), ({"asset_type": "etf"}, None),
                                 ({"notes_add": "Dividend payer."}, "AI analyst reads")):
            with self.subTest(changes=changes):
                card, _ = one({"type": "holding.update", "holding": "OTP", "changes": changes})
                if careful:
                    self.assertIn(careful, card["confirm"])
                else:
                    self.assertNotIn("confirm", card)
                self.assertNotIn("quantity", card["apply"])  # a description save never carries units
        card, notes = one({"type": "holding.update", "holding": "OTP", "changes": {"name": "apple"}})
        self.assertIn("another holding already has that name", notes[0])

    def test_notes_are_added_to_and_unknown_holdings_refused(self):
        card, _ = one({"type": "holding.update", "holding": "OTP", "changes": {"notes_add": "Dividend payer."}})
        self.assertEqual(card["apply"]["notes"], "Long term. Dividend payer.")
        # the card keeps the notes it was made against: applied once, or cut by the user since, it adds nothing
        self.assertEqual(card["payload"]["changes"], {"notes_add": "Dividend payer.", "notes_was": "Long term."})
        st = state()
        self.assertTrue(proposals.preview("holding.update", card["payload"], st)["ok"])
        for notes in ("Long term. Dividend payer.", "Sold half in March.", ""):  # applied; rewritten; cleared
            with self.subTest(notes=notes):
                st["holdings"]["OTP"]["notes"] = notes
                self.assertIn("notes changed since", proposals.preview("holding.update", card["payload"], st)["reason"])
        st["holdings"]["OTP"]["notes"] = "Line one.\nLine two."  # notes typed in the form keep their line breaks
        card, _ = one({"type": "holding.update", "holding": "OTP", "changes": {"notes_add": "Dividend payer."}}, st)
        self.assertTrue(proposals.preview("holding.update", card["payload"], st)["ok"])
        for obj in ({"holding": "NOPE", "changes": {"name": "x"}}, {"holding": "OTP", "changes": {"symbol": "X"}},
                    {"holding": "OTP", "changes": {"name": "OTP Bank"}}):
            with self.subTest(obj=obj):
                card, notes = one({"type": "holding.update", **obj})
                self.assertIsNone(card)
                self.assertEqual(len(notes), 1)


class PurchaseTests(unittest.TestCase):
    def test_a_purchase_shows_the_units_held_after(self):
        card, notes = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 5,
                           "unit_price": 200, "paid_amount": 1003.5, "paid_currency": "usd", "platform": "Revolut"})
        self.assertEqual(notes, [])
        self.assertEqual(card["apply"], {"instrument_id": "AAPL", "side": "buy", "buy_date": "2025-03-03", "quantity": 5.0,
                                         "unit_price": 200.0, "paid_amount": 1003.5, "paid_currency": "USD", "platform": "Revolut"})
        held = next(r for r in card["changes"] if r["label"] == "Units held")
        self.assertEqual((held["before"], held["after"], held["derived"]), ("10", "15", True))
        self.assertNotIn("confirm", card)  # a buy adds; nothing is lost

    def test_paid_defaults_to_the_holdings_currency_and_prices_show_their_unit(self):
        card, _ = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1,
                       "unit_price": 200, "paid_amount": 201})
        self.assertEqual(card["apply"]["paid_currency"], "USD")  # not the reporting currency (HUF)
        self.assertIn({"label": "Paid", "before": None, "after": "201 USD"}, card["changes"])
        st = state()
        st["holdings"]["AAPL"].update(currency="GBP", price_divisor=100.0)
        card, _ = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1, "unit_price": 7500}, st)
        self.assertIn({"label": "Price per unit", "before": None, "after": "7 500 GBp (100 = 1 GBP)"}, card["changes"])
        self.assertIsNone(card["apply"]["paid_currency"])  # nothing paid given: nothing stored about it

    def test_a_sale_asks_first(self):
        card, _ = one({"type": "purchase.add", "holding": "AAPL", "side": "sell", "date": "2025-03-03", "quantity": 4, "unit_price": 220})
        self.assertEqual(card["confirm"], "This records a sale. Apply it?")
        self.assertEqual(proposals.preview("purchase.add", card["payload"], state())["confirm"], card["confirm"])

    def test_the_first_transaction_replaces_the_units_on_the_holding(self):
        card, _ = one({"type": "purchase.add", "holding": "OTP", "side": "buy", "date": "2025-03-03", "quantity": 2, "unit_price": 21000})
        held = next(r for r in card["changes"] if r["label"] == "Units held")
        self.assertEqual(held["after"], "2")
        self.assertTrue(next(r for r in card["changes"] if r["label"] == "Note")["derived"])
        self.assertIn("replaces the 5 units on the holding with 2", card["confirm"])  # 5 -> 2: asks
        card, _ = one({"type": "purchase.add", "holding": "OTP", "side": "buy", "date": "2025-03-03", "quantity": 5, "unit_price": 21000})
        # 5 -> 5 units, but the transaction's cost replaces the 20 000 on the holding: that asks
        self.assertEqual(card["confirm"], "This replaces the average cost on the holding with its transactions' cost. Apply it?")
        self.assertIn("not the 5 units at 20 000 HUF on the holding", card["changes"][-1]["after"])
        st = state()
        st["holdings"]["OTP"]["avg_cost"] = None
        card, _ = one({"type": "purchase.add", "holding": "OTP", "side": "buy", "date": "2025-03-03", "quantity": 5, "unit_price": 21000}, st)
        self.assertNotIn("confirm", card)  # 5 -> 5 and no cost on record: the record simply becomes its transaction

    def test_two_identical_trades_are_named_not_merged_silently(self):
        trade = {"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1, "unit_price": 200, "reason": "x"}
        _, cards, notes = proposals.parse(block(trade) + block(trade), state())
        self.assertEqual(len(cards), 1)
        self.assertIn("keeps one of two identical transactions", notes[0])
        rule = {"type": "rules.add", "rule": "No leverage.", "reason": "x"}
        _, cards, notes = proposals.parse(block(rule) + block(rule), state())
        self.assertEqual((len(cards), notes), (1, []))  # the same rule twice is simply one card

    def test_impossible_transactions_are_refused(self):
        for obj, why in (
            ({"holding": "AAPL", "side": "sell", "date": "2025-03-03", "quantity": 11, "unit_price": 1}, "more units than"),
            ({"holding": "OTP", "side": "sell", "date": "2025-03-03", "quantity": 1, "unit_price": 1}, "can't be sold from"),
            ({"holding": "AAPL", "side": "buy", "date": "2999-01-01", "quantity": 1, "unit_price": 1}, "in the future"),
            ({"holding": "AAPL", "side": "buy", "date": "2025-02-30", "quantity": 1, "unit_price": 1}, "not a real date"),
            ({"holding": "AAPL", "side": "gift", "date": "2025-03-03", "quantity": 1, "unit_price": 1}, "buy or a sell"),
            ({"holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 0, "unit_price": 1}, "more than 0"),
            ({"holding": "AAPL", "side": "buy", "date": "1899-12-31", "quantity": 1, "unit_price": 1}, "before 1900"),
            ({"holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1, "unit_price": 1, "paid_amount": 0}, "more than 0"),
            ({"holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1, "unit_price": 0}, "neither a price"),
            ({"holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1, "unit_price": 1, "paid_amount": 5,
              "paid_currency": "GBX"}, "is a price unit"),
        ):
            with self.subTest(why=why):
                card, notes = one({"type": "purchase.add", **obj})
                self.assertIsNone(card)
                self.assertIn(why, notes[0])


class DeleteTests(unittest.TestCase):
    def test_deletes_name_what_goes_and_carry_the_confirmation(self):
        card, _ = one({"type": "holding.delete", "holding": "AAPL"})
        self.assertEqual(card["apply"], {"instrument_id": "AAPL", "confirm_purchases": 1})
        self.assertEqual(card["confirm"], proposals.CONFIRM_DELETE)
        card_rule, _ = one({"type": "rules.remove", "rule": "No leverage."}, state(rules=["No leverage."]))
        self.assertEqual(card_rule["confirm"], "Remove this rule?")
        self.assertIn("with its 1 transaction", card["changes"][0]["before"])
        card, _ = one({"type": "purchase.delete", "holding": "AAPL", "date": "2024-01-02", "side": "buy", "quantity": 10})
        self.assertEqual(card["apply"], {"lot_id": 7, "instrument_id": "AAPL"})
        card, notes = one({"type": "purchase.delete", "holding": "AAPL", "date": "2024-01-03", "side": "buy", "quantity": 10})
        self.assertIsNone(card)
        self.assertIn("no single transaction", notes[0])

    def test_one_deletion_per_answer_of_any_kind(self):
        text = block({"type": "holding.delete", "holding": "OTP", "reason": "x"}) + block(
            {"type": "purchase.delete", "holding": "AAPL", "date": "2024-01-02", "side": "buy", "quantity": 10, "reason": "x"})
        _, cards, notes = proposals.parse(text, state())
        self.assertEqual(len(cards), 1)
        self.assertIn("only one removal or deletion", notes[0])

    def test_preview_rebuilds_the_confirmation_from_today(self):
        st = state()
        st["holdings"]["AAPL"]["lots"].append({"lot_id": 8, "date": "2025-01-02", "side": "buy", "quantity": 1.0, "unit_price": 190.0})
        out = proposals.preview("holding.delete", {"holding": "AAPL"}, st)
        self.assertEqual(out["apply"], {"instrument_id": "AAPL", "confirm_purchases": 2})


class ImportTests(unittest.TestCase):
    def test_an_import_may_hold_25_cards_a_plain_answer_5(self):
        many = "".join(block({"type": "holding.add", "name": f"Fund {i}", "asset_type": "mutual_fund", "currency": "HUF",
                              "quantity": i + 1, "unit_price": 1000, "reason": "From your screenshot."}) for i in range(30))
        self.assertEqual(len(proposals.parse(many, state())[1]), 5)
        _, cards, notes = proposals.parse(many, state(), proposals.MAX_IMPORT_PROPOSALS)
        self.assertEqual(len(cards), 25)
        self.assertIn("only 25 suggestions", notes[0])
        self.assertEqual(len({c["apply"]["instrument_id"] for c in cards}), 25)  # distinct ids for distinct names


class SymbolCheckTests(unittest.TestCase):
    def setUp(self):
        from analyzer import advisor
        advisor._quote_cache.clear()
        self.addCleanup(advisor._quote_cache.clear)

    def test_network_trouble_is_not_an_unknown_symbol(self):
        # yfinance reports a dropped connection as a missing price: whether the source answers at all decides
        from analyzer import symbols
        missing = {"price": None, "error": "No live price returned."}
        for up, status in ((False, 503), (True, 404)):
            with self.subTest(up=up), mock.patch("analyzer.market.live_quote", return_value=missing), \
                    mock.patch("analyzer.market.reachable", return_value=up):
                with self.assertRaises(SymbolError) as caught:
                    symbols.quote_symbol("VWCE.DE")
                self.assertEqual(caught.exception.status, status)
        empty = (pd.DataFrame(columns=["date", "close", "currency"]), "", "OTP: boom; BAMOSZ: no data")
        with mock.patch("analyzer.market.download_fund_nav", return_value=empty), \
                mock.patch("analyzer.market.reachable", side_effect=lambda url: "bamosz" in url):
            with self.assertRaises(SymbolError) as caught:
                symbols.quote_symbol("fund:HU0000728670")
            self.assertEqual(caught.exception.status, 503)

    def test_a_symbol_must_look_like_one(self):
        self.assertEqual(proposals.clean_symbol("vwce.de"), "VWCE.DE")
        self.assertEqual(proposals.clean_symbol("fund:hu0000728670"), "fund:HU0000728670")
        for bad in ("AAPL?x=1", "../v7/x", "fund:nope", "A B"):
            with self.subTest(bad=bad), self.assertRaises(proposals.Invalid):
                proposals.clean_symbol(bad)

    def test_a_card_that_couldnt_be_checked_may_be_retried(self):
        out = proposals.preview("holding.add", {"name": "Down", "asset_type": "etf", "symbol": "DOWN.DE"}, state())
        self.assertEqual((out["ok"], out["retry"]), (False, True))
        out = proposals.preview("holding.add", {"name": "Mystery", "asset_type": "etf", "symbol": "NOPE.XX"}, state())
        self.assertNotIn("retry", out)

    @staticmethod
    def _no_db(advisor):
        return (mock.patch.object(advisor, "_holdings_state", return_value={}),
                mock.patch.object(advisor.db, "base_currency", return_value="HUF"))

    def test_an_import_waits_for_symbol_checks_only_so_long(self):
        from analyzer import advisor
        release, asked = threading.Event(), []

        def quote(symbol):
            asked.append(symbol)
            if symbol == "SLOW.DE":
                release.wait(5)
            return {"price": 10.0, "currency": "EUR", "quote_currency": "EUR", "divisor": 1.0}

        text = "".join(block({"type": "holding.add", "name": f"Fund {s}", "asset_type": "etf", "symbol": s, "reason": "x"})
                       for s in ("A.DE", "SLOW.DE", "B.DE", "a.de", "C.DE", "LATE.DE"))
        settings = {"rules": [], "preferences": normalize_preferences({})}
        holdings, base = self._no_db(advisor)
        with holdings, base, mock.patch.object(advisor.symbols, "quote_symbol", side_effect=quote), \
                mock.patch.object(advisor, "PREFETCH_SECONDS", 0.3):
            st = advisor.proposal_state(settings)
            started = time.monotonic()
            advisor._prefetch_quotes(text, st, proposals.MAX_PROPOSALS)
            self.assertLess(time.monotonic() - started, 3)  # gave up on SLOW.DE instead of waiting for it
            self.assertNotIn("LATE.DE", asked)  # the sixth block is past the limit: never asked about
            self.assertEqual(sorted(asked), ["A.DE", "B.DE", "C.DE", "SLOW.DE"])  # a.de is A.DE: asked once
            release.set()
            _, cards, notes = proposals.parse(text, st, proposals.MAX_PROPOSALS)
        self.assertEqual([c["apply"]["market_symbol"] for c in cards], ["A.DE", "B.DE", "C.DE"])
        self.assertEqual(notes, ["A suggested change couldn't be shown as a button: its symbol couldn't be checked right "
                                 "now (no connection to the price source); ask again in a moment.",
                                 "A suggested change couldn't be shown as a button: another suggestion in this answer adds "
                                 "a holding with that name.",
                                 "A suggested change couldn't be shown as a button: only 5 suggestions are shown per answer."])

    def test_after_the_prefetch_nothing_more_goes_to_the_network(self):
        from analyzer import advisor
        holdings, base = self._no_db(advisor)
        with holdings, base, mock.patch.object(advisor.symbols, "quote_symbol") as network:
            st = advisor.proposal_state({"rules": [], "preferences": normalize_preferences({})})
            advisor._prefetch_quotes("no cards", st, proposals.MAX_PROPOSALS)
            with self.assertRaises(SymbolError) as caught:
                st["quote"]("VWCE.DE")
            self.assertEqual(caught.exception.status, 503)
            network.assert_not_called()

    def test_a_found_quote_is_reused_for_a_while(self):
        from analyzer import advisor
        found = {"price": 10.0, "currency": "EUR", "quote_currency": "EUR", "divisor": 1.0}
        with mock.patch.object(advisor.symbols, "quote_symbol", return_value=found) as network:
            self.assertEqual(advisor._cached_quote("VWCE.DE"), found)
            self.assertEqual(advisor._cached_quote("vwce.de"), found)
            self.assertEqual(network.call_count, 1)
        with mock.patch.object(advisor.symbols, "quote_symbol", side_effect=SymbolError("No price.", 404)) as network:
            for _ in range(2):
                with self.assertRaises(SymbolError):
                    advisor._cached_quote("NOPE.XX")
            self.assertEqual(network.call_count, 2)  # a refusal is never remembered


class AnswerOrderTests(unittest.TestCase):
    """An answer's cards are checked in order, each against what the earlier ones leave (as Apply all applies them)."""

    def test_several_purchases_of_one_holding_show_the_running_units(self):
        buys = "".join(block({"type": "purchase.add", "holding": "OTP", "side": "buy", "date": d, "quantity": q,
                              "unit_price": 21000, "reason": "x"}) for d, q in (("2024-01-01", 5), ("2024-06-01", 5)))
        _, cards, _ = proposals.parse(buys, state())
        held = [next(r for r in c["changes"] if r["label"] == "Units held") for c in cards]
        self.assertEqual([(h["before"], h["after"]) for h in held], [("5", "5"), ("5", "10")])
        buys = "".join(block({"type": "purchase.add", "holding": "OTP", "side": "buy", "date": d, "quantity": q,
                              "unit_price": 21000, "reason": "x"}) for d, q in (("2024-01-01", 2), ("2024-06-01", 3)))
        _, cards, _ = proposals.parse(buys, state())
        self.assertIn("replaces the 5 units on the holding with 2", cards[0]["confirm"])
        self.assertNotIn("confirm", cards[1])  # the second buy is an ordinary buy once the first is in
        self.assertFalse(any(r["label"] == "Note" for r in cards[1]["changes"]))

    def test_one_answer_adds_a_holding_once(self):
        adds = "".join(block({"type": "holding.add", "name": n, "asset_type": "mutual_fund", "currency": "HUF", "reason": "x"})
                       for n in ("Fund A", "fund a"))
        _, cards, notes = proposals.parse(adds, state())
        self.assertEqual(len(cards), 1)
        self.assertIn("another suggestion in this answer adds a holding with that name", notes[0])

    def test_the_callers_state_is_left_alone(self):
        st = state()
        proposals.parse(block({"type": "holding.add", "name": "Fund A", "asset_type": "mutual_fund", "currency": "HUF",
                               "reason": "x"}), st)
        self.assertEqual(sorted(st["holdings"]), ["AAPL", "OTP"])


class ShownValueTests(unittest.TestCase):
    def test_costs_and_prices_carry_their_unit(self):
        st = state()
        st["holdings"]["AAPL"].update(currency="GBP", price_divisor=100.0)
        card, _ = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1,
                       "unit_price": 75.12}, st)
        self.assertIn({"label": "Price per unit", "before": None, "after": "75,12 GBp (100 = 1 GBP)"}, card["changes"])
        card, _ = one({"type": "purchase.delete", "holding": "AAPL", "date": "2024-01-02", "side": "buy", "quantity": 10})
        self.assertEqual(card["changes"][0]["before"], "Buy of 10 on 2024-01-02 at 180 USD")

    def test_odd_input_is_refused_or_shown_as_it_is(self):
        arabic = "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in "2025-03-03")
        card, notes = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": arabic, "quantity": 1, "unit_price": 1})
        self.assertIn("not a YYYY-MM-DD date", notes[0])
        st = state()
        st["holdings"]["AAPL"]["currency"] = "GBX"  # a CSV import can leave a price unit as a holding's currency
        card, notes = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1,
                           "unit_price": 1, "paid_amount": 2}, st)
        self.assertIn("is a price unit", notes[0])
        self.assertEqual((proposals._num(4e-7), proposals._num(-0.0), proposals._num(-1e-10)), ("0,0000004", "0", "-1e-10"))


class ReviewCycleTwoTests(unittest.TestCase):
    """Second review of phase 3: rate limits, pinned ids, far-off costs, in-order checks beyond holdings."""

    def setUp(self):
        from analyzer import advisor
        advisor._quote_cache.clear()
        self.addCleanup(advisor._quote_cache.clear)

    def test_a_rate_limit_is_not_no_price(self):
        import urllib.error
        from analyzer import market, symbols
        limited = {"price": None, "error": "Quote failed: Too Many Requests. Rate limited."}
        with mock.patch("analyzer.market.live_quote", return_value=limited), \
                mock.patch("analyzer.market.reachable", return_value=True):
            with self.assertRaises(SymbolError) as caught:
                symbols.quote_symbol("VWCE.DE")
            self.assertEqual(caught.exception.status, 503)
        for code, answers in ((404, True), (429, False), (503, False)):
            with self.subTest(code=code), mock.patch("urllib.request.urlopen",
                                                     side_effect=urllib.error.HTTPError("u", code, "x", {}, None)):
                self.assertIs(market.reachable("https://example.invalid/"), answers)

    def test_a_new_holdings_id_is_fixed_by_its_card(self):
        card, _ = one({"type": "holding.add", "name": "Alpha Xetra", "asset_type": "stock", "currency": "EUR"})
        self.assertEqual(card["payload"]["id"], "ALPHA-XETRA")
        st = state()
        st["holdings"]["ALPHA-XETRA"] = {**st["holdings"]["OTP"], "name": "Something else", "symbol": ""}
        out = proposals.preview("holding.add", card["payload"], st)  # another card took the id first: refused, not renumbered
        self.assertIn("taken the id ALPHA-XETRA", out["reason"])
        card, notes = one({"type": "holding.add", "name": "X", "asset_type": "stock", "currency": "EUR", "id": "../x"})
        self.assertIn("not one Moneylyzer makes", notes[0])

    def test_a_cost_or_price_far_off_the_last_price_asks_first(self):
        st = state()
        st["holdings"]["AAPL"].update(currency="GBP", price_divisor=100.0, last_price=7500.0)
        trade = {"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1}
        card, _ = one({**trade, "unit_price": 75.12}, st)  # pounds, not pence
        self.assertIn("over 30 times off the last price", card["confirm"])
        self.assertTrue(any(r["label"] == "Check" for r in card["changes"]))
        card, _ = one({**trade, "unit_price": 7400}, st)
        self.assertNotIn("confirm", card)
        st["holdings"]["AAPL"].update(currency="USD", price_divisor=1.0, last_price=200.0)
        card, _ = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1,
                       "unit_price": 20000}, st)
        self.assertIn("price per unit over 30 times off", card["confirm"])

    def test_the_prefetch_asks_only_for_cards_that_could_be_made(self):
        from analyzer import advisor
        text = "".join(block(b) for b in (
            {"type": "holding.add", "name": "Ok", "asset_type": "etf", "symbol": "OK.DE", "reason": "x"},
            {"type": "holding.add", "name": "Extra", "asset_type": "etf", "symbol": "EXTRA.DE", "reason": "x", "evil": 1},
            {"type": "holding.add", "name": "Mine", "asset_type": "etf", "symbol": "OTP.BD", "reason": "x"}))
        st = {**state(), "quotes": {}, "frozen": False}
        asked = []
        st["quote"] = lambda s: asked.append(s) or {"price": 1.0, "currency": "EUR", "quote_currency": "EUR", "divisor": 1.0}
        advisor._prefetch_quotes(text, st, proposals.MAX_PROPOSALS)
        self.assertEqual(asked, ["OK.DE"])

    def test_rules_and_notes_are_checked_in_order_too(self):
        st = state(preferences={**normalize_preferences({}), "notes": "N"})
        notes = "".join(block({"type": "preferences.update", "changes": {"notes_add": a}, "reason": "x"}) for a in ("A", "B"))
        _, cards, _ = proposals.parse(notes, st)
        self.assertEqual([c["payload"]["changes"]["notes"] for c in cards], ["N A", "N A B"])
        st["preferences"] = {**st["preferences"], "notes": "N A"}  # the first card applied
        self.assertTrue(proposals.preview("preferences.update", cards[1]["payload"], st)["ok"])
        from analyzer.persona import MAX_RULES
        rules = "".join(block({"type": "rules.add", "rule": r, "reason": "x"}) for r in ("Rule A", "Rule B"))
        _, cards, notes = proposals.parse(rules, state(rules=[f"r{i}" for i in range(MAX_RULES - 1)]))
        self.assertEqual(len(cards), 1)
        self.assertIn("most rules there can be", notes[0])
        same = block({"type": "rules.add", "rule": "Rule A", "reason": "x"})
        _, cards, notes = proposals.parse(same + same, state())
        self.assertEqual((len(cards), notes), (1, []))  # the same block twice: one card, nothing to say

    def test_numbers_stay_readable_and_bounded(self):
        self.assertEqual(proposals._num(0.12345671), "0,12345671")
        card, notes = one({"type": "holding.add", "name": "Big", "asset_type": "stock", "currency": "EUR", "quantity": 1e300})
        self.assertIn("too large", notes[0])

    def test_a_purchase_without_an_amount_paid_needs_no_paid_currency(self):
        st = state()
        st["holdings"]["AAPL"]["currency"] = "GBX"
        card, notes = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "date": "2025-03-03", "quantity": 1,
                           "unit_price": 1}, st)
        self.assertEqual(notes, [])
        self.assertIsNone(card["apply"]["paid_currency"])


class ReviewCycleThreeTests(unittest.TestCase):
    """Third review of phase 3: error classes per source, far-off prices for new holdings, the prefetch's in-order run."""

    def test_no_price_only_when_every_source_answered(self):
        from analyzer import symbols
        for error, status in (("OTP Alapkezelő: no data; BAMOSZ: no data or no currency", 404),
                              ("OTP Alapkezelő: no data; BAMOSZ: HTTP Error 503: Service Unavailable", 503)):
            empty = (pd.DataFrame(columns=["date", "close", "currency"]), "", error)
            with self.subTest(status=status), mock.patch("analyzer.market.download_fund_nav", return_value=empty):
                with self.assertRaises(SymbolError) as caught:
                    symbols.quote_symbol("fund:HU0000728670")
                self.assertEqual(caught.exception.status, status)
        missing = {"price": None, "error": "No live price returned."}
        with mock.patch("analyzer.market.live_quote", return_value=missing), \
                mock.patch("analyzer.market.reachable", return_value=True) as probe:
            with self.assertRaises(SymbolError):
                symbols.quote_symbol("VWCE.DE")
        self.assertEqual(probe.call_args.args[0], "https://query1.finance.yahoo.com/v8/finance/chart/VWCE.DE")

    def test_a_price_for_a_holding_without_a_stored_close_is_checked_against_its_quote(self):
        text = block({"type": "holding.add", "name": "Vanguard S&P 500", "asset_type": "etf", "symbol": "VUSA.L",
                      "reason": "x"}) + block({"type": "purchase.add", "holding": "VUSA", "side": "buy",
                                               "date": "2025-03-03", "quantity": 1, "unit_price": 75.12, "reason": "x"})
        _, cards, _ = proposals.parse(text, state())
        self.assertIn("over 30 times off", cards[1]["confirm"])  # pounds for a holding quoted at 9 000 pence

    def test_the_prefetch_asks_what_the_in_order_check_will_ask(self):
        long_reason = {"type": "holding.add", "name": "A", "asset_type": "etf", "symbol": "LONG.DE", "reason": "x" * 301}
        twin = [{"type": "holding.add", "name": "Twin", "asset_type": "etf", "symbol": s, "reason": "x"} for s in ("T1.DE", "T2.DE")]
        freed = [{"type": "holding.update", "holding": "OTP", "changes": {"name": "OTP Nyrt."}, "reason": "x"},
                 {"type": "holding.add", "name": "OTP Bank", "asset_type": "stock", "symbol": "OTPNEW.DE", "reason": "x"}]
        text = "".join(block(b) for b in [long_reason, *twin, *freed])
        self.assertEqual(proposals.symbols_to_check(text, state(), proposals.MAX_PROPOSALS), ["T1.DE", "OTPNEW.DE"])

    def test_a_repeated_refused_block_is_one_note(self):
        down = block({"type": "holding.add", "name": "Down", "asset_type": "etf", "symbol": "DOWN.DE", "reason": "x"})
        _, cards, notes = proposals.parse(down + down, state())
        self.assertEqual((cards, len(notes)), ([], 1))


class BuysOnlyTests(unittest.TestCase):
    """Units and cost only from buys and sells (owner decision 2026-11-26): the review of that change."""

    def test_two_undated_trades_stay_two_cards(self):
        # "jan. 2: buy 10 @ 180" and "febr. 3: buy 10 @ 180" without a year look alike until the user dates them
        trade = {"type": "purchase.add", "holding": "AAPL", "side": "buy", "quantity": 10, "unit_price": 180, "reason": "x"}
        _, cards, notes = proposals.parse(block(trade) + block(trade), state())
        self.assertEqual((len(cards), notes), (2, []))

    def test_quantity_zero_is_an_empty_holding(self):
        card, notes = one({"type": "holding.add", "name": "Watch", "asset_type": "stock", "currency": "USD", "quantity": 0})
        self.assertEqual(notes, [])
        self.assertNotIn("buy", card["apply"])

    def test_a_dateless_card_is_not_applied_without_its_date(self):
        card, _ = one({"type": "purchase.add", "holding": "AAPL", "side": "buy", "quantity": 1, "unit_price": 180})
        out = proposals.preview("purchase.add", card["payload"], state())
        self.assertEqual((out["ok"], out["reason"]), (False, "Pick the date on the card first."))
        self.assertTrue(proposals.preview("purchase.add", {**card["payload"], "date": "2025-03-03"}, state())["ok"])


if __name__ == "__main__":
    unittest.main()
