"""API tests: real FastAPI app, temporary database, no network (market sources faked)."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
from fastapi.testclient import TestClient

import json

from analyzer import db, llm
from server import auth, live

H = {"X-Requested-With": "lia"}


def fake_quote(symbol):
    table = {"USDEUR=X": (0.9, "EUR", 0.9), "USDHUF=X": (336.0, "HUF", 335.0), "SEC0.DE": (13.6, "EUR", 13.4)}
    if symbol not in table:
        return {"price": None, "currency": None, "time": None, "prev_close": None, "error": "fake: unknown"}
    p, c, prev = table[symbol]
    return {"price": p, "currency": c, "time": pd.Timestamp("2026-11-24 14:00", tz="UTC"), "prev_close": prev, "error": None}


def fake_fx_history(currency):
    days = pd.bdate_range("2024-01-01", "2026-11-23")
    return pd.DataFrame({"date": days, "per_usd": {"EUR": 0.85, "HUF": 340.0}.get(currency, 1.0)}), None


def fake_daily(symbol, period="5y"):
    days = pd.bdate_range("2026-10-01", "2026-11-23")
    return pd.DataFrame({"date": days, "close": 13.0, "currency": "EUR"}), None


def fake_fund(isin, start):
    return pd.DataFrame({"date": pd.to_datetime(["2026-11-19", "2026-11-20"]), "close": [3.376631, 3.289577],
                         "currency": "HUF"}), "OTP Alapkezelő", None


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3"),
            # the fixtures end on 2026-11-23: pin "today" so range assertions do not rot with the calendar
            mock.patch("analyzer.portfolio._today", return_value=pd.Timestamp("2026-11-24")),
            mock.patch.object(live, "live_quote", fake_quote), mock.patch.object(live, "download_fx_history", fake_fx_history),
            mock.patch("server.main.live_quote", fake_quote),
            mock.patch.object(live, "download_daily_history", fake_daily), mock.patch.object(live, "download_fund_nav", fake_fund),
            mock.patch.object(auth, "_setup_code", None), mock.patch.object(auth, "_failures", {}),
            mock.patch("analyzer.research.fetch_feed", return_value=[]),
            mock.patch.object(live.yf, "Ticker", return_value=mock.Mock(news=[])),
        ]
        for p in self.patches:
            p.start()
        live.hub.__init__()
        from server.main import app
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def setup_account(self):
        code = auth._setup_code
        r = self.client.post("/api/auth/setup", json={"username": "owner", "password": "correct horse 1", "code": code}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_saved_conversation_endpoints(self):
        self.setup_account()
        from analyzer import advisor
        advisor.save_turn("conv-api-01", "claude-login", "Hello?", {"text": "Hi", "model": "m", "sources": []})
        listed = self.client.get("/api/advisor/conversations").json()
        self.assertEqual([c["id"] for c in listed], ["conv-api-01"])
        self.assertNotIn("messages", listed[0])
        self.assertEqual(len(self.client.get("/api/advisor/conversations/conv-api-01").json()["messages"]), 2)
        self.assertEqual(self.client.get("/api/advisor/conversations/conv-nope-1").status_code, 404)
        self.assertEqual(self.client.get("/api/advisor/conversations/bad!id").status_code, 422)
        self.assertEqual(self.client.delete("/api/advisor/conversations/conv-api-01").status_code, 403)  # CSRF header
        self.assertEqual(self.client.delete("/api/advisor/conversations/conv-api-01", headers=H).json(), {"ok": True})
        self.assertEqual(self.client.delete("/api/advisor/conversations/conv-api-01", headers=H).status_code, 404)
        advisor.save_turn("conv-api-02", "claude-login", "Again", {"text": "x", "model": "m", "sources": []})
        self.assertEqual(self.client.delete("/api/advisor/conversations", headers=H).json(), {"deleted": 1})
        r = self.client.post("/api/advisor/ask", headers=H, json={"question": "q", "conversation_id": "x"})
        self.assertEqual(r.status_code, 422)  # too short an id
        self.assertEqual(self.client.delete("/api/advisor/conversations").status_code, 403)  # Delete all needs CSRF too
        # a failing save never costs the answer
        with mock.patch("analyzer.advisor.complete", return_value={"text": "kept", "model": "m", "sources": []}), \
                mock.patch("analyzer.advisor.save_turn", side_effect=RuntimeError("disk full")):
            r = self.client.post("/api/advisor/ask", headers=H, json={"question": "q", "conversation_id": "conv-api-03"})
        self.assertEqual((r.status_code, r.json()["text"]), (200, "kept"))

    def test_ask_passes_the_chat_effort(self):
        self.setup_account()
        answer = {"text": "ok", "model": "m", "sources": [], "provider": {}, "effort": "max", "shared": {}}
        with mock.patch("server.main.advisor.ask", return_value=answer) as asked:
            r = self.client.post("/api/advisor/ask", headers=H, json={"question": "q", "effort": "max"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(asked.call_args.kwargs["effort"], "max")
            self.client.post("/api/advisor/ask", headers=H, json={"question": "q"})
            self.assertIsNone(asked.call_args.kwargs["effort"])
            self.assertEqual(self.client.post("/api/advisor/ask", headers=H, json={"question": "q", "effort": "ultra"}).status_code, 422)

    def test_model_list_uses_typed_or_saved_key(self):
        self.setup_account()
        pid = self.client.post("/api/ai/providers", headers=H, json={
            "name": "API", "kind": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-saved"}).json()["id"]
        listing = {"models": ["claude-opus-5-5"], "total": 1}
        with mock.patch("server.main.llm.list_models", return_value=listing) as lm:
            r = self.client.post("/api/ai/models", headers=H, json={"kind": "anthropic", "provider_id": pid})
            self.assertEqual(r.json(), listing)
            self.assertEqual(lm.call_args.args, ("anthropic", "", "sk-saved"))
            self.client.post("/api/ai/models", headers=H, json={"kind": "anthropic", "provider_id": pid, "api_key": "sk-typed"})
            self.assertEqual(lm.call_args.args[2], "sk-typed")
            calls = lm.call_count
            # The saved key only goes where it was saved for: not to an edited URL, not as another provider type.
            for moved in ({"kind": "anthropic", "base_url": "https://elsewhere.example.com"},
                          {"kind": "openai_compat", "base_url": "https://any.example.com/v1"}):
                r = self.client.post("/api/ai/models", headers=H, json={**moved, "provider_id": pid})
                self.assertEqual(r.status_code, 400, moved)
                self.assertIn("type the API key again", r.json()["detail"])
            self.assertEqual(lm.call_count, calls)
        self.assertEqual(self.client.post("/api/ai/models", headers=H, json={"kind": "anthropic", "api_key": "sk-a\nb"}).status_code, 422)
        with mock.patch("server.main.llm.list_models", side_effect=llm.LLMError("nope")):
            self.assertEqual(self.client.post("/api/ai/models", headers=H, json={"kind": "openai_compat"}).status_code, 400)
        self.assertEqual(self.client.post("/api/ai/models", headers=H, json={"kind": "claude_code"}).status_code, 422)
        self.assertEqual(self.client.post("/api/ai/models", json={"kind": "anthropic"}).status_code, 403)  # CSRF header

    def test_setup_needs_code_and_happens_once(self):
        self.assertTrue(self.client.get("/api/auth/state").json()["setup_required"])
        r = self.client.post("/api/auth/setup", json={"username": "x", "password": "correct horse 1", "code": "WRONG"}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.setup_account()
        self.assertEqual(self.client.get("/api/auth/state").json()["user"], "owner")
        r = self.client.post("/api/auth/setup", json={"username": "y", "password": "correct horse 1", "code": "X"}, headers=H)
        self.assertEqual(r.status_code, 409)

    def test_auth_required_csrf_and_rate_limit(self):
        self.setup_account()
        anon = TestClient(self.client.app)
        self.assertEqual(anon.get("/api/portfolio").status_code, 401)
        self.assertEqual(self.client.post("/api/instruments", json={}).status_code, 403)  # no CSRF header
        for _ in range(5):
            self.assertEqual(anon.post("/api/auth/login", json={"username": "owner", "password": "nope"}, headers=H).status_code, 401)
        self.assertEqual(anon.post("/api/auth/login", json={"username": "owner", "password": "correct horse 1"}, headers=H).status_code, 429)

    def test_meta_donate_link_configurable(self):
        self.assertEqual(self.client.get("/api/meta").json()["donate_url"], "https://revolut.me/vargamolnarb")
        self.assertEqual(self.client.get("/api/meta").json()["repository"], "https://github.com/vargamolnarbertalan/moneylyzer")
        with mock.patch.dict("os.environ", {"LIA_DONATE_URL": ""}):
            self.assertIsNone(self.client.get("/api/meta").json()["donate_url"])
        with mock.patch.dict("os.environ", {"LIA_DONATE_URL": "javascript:alert(1)"}):
            self.assertIsNone(self.client.get("/api/meta").json()["donate_url"])

    def test_advisor_rules_saved_on_their_own(self):
        self.setup_account()
        self.client.put("/api/ai/settings", json={"preferences": {"risk": "growth"}}, headers=H).raise_for_status()
        self.assertEqual(self.client.put("/api/ai/settings", json={"rules": ["x"]}, headers=H).status_code, 422)  # no rules_expected
        r = self.client.put("/api/ai/settings", json={"rules": ["Only EUR holdings", "  only EUR   holdings "], "rules_expected": []}, headers=H)
        self.assertEqual(r.json()["rules"], ["Only EUR holdings"])
        self.assertEqual(r.json()["preferences"]["risk"], "growth")      # a rules-only save keeps preferences
        cur = ["Only EUR holdings"]
        self.assertEqual(self.client.put("/api/ai/settings", json={"rules": ["r"] * 31, "rules_expected": cur}, headers=H).status_code, 422)
        self.assertEqual(self.client.put("/api/ai/settings", json={"rules": ["x" * 301], "rules_expected": cur}, headers=H).status_code, 422)
        self.assertEqual(self.client.get("/api/ai/settings").json()["rules"], ["Only EUR holdings"])
        # stale client: it saw no rules, but another device added one meanwhile -> 409, nothing lost
        stale = self.client.put("/api/ai/settings", json={"rules": ["Other rule"], "rules_expected": []}, headers=H)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get("/api/ai/settings").json()["rules"], ["Only EUR holdings"])
        fresh = self.client.put("/api/ai/settings", json={"rules": ["Only EUR holdings", "Acc only"],
                                                           "rules_expected": ["Only EUR holdings"]}, headers=H)
        self.assertEqual(fresh.json()["rules"], ["Only EUR holdings", "Acc only"])

    def test_view_prefs_sync_per_key_and_reject_unknowns(self):
        self.setup_account()
        self.assertEqual(self.client.get("/api/prefs").json(), {})
        r = self.client.patch("/api/prefs", json={"overview_range": "1W", "alloc_view": "pie"}, headers=H)
        self.assertEqual(r.json(), {"overview_range": "1W", "alloc_view": "pie"})
        # a second device changes another key: the first one's choice survives
        self.client.patch("/api/prefs", json={"theme": "light", "motion": "reduced"}, headers=H).raise_for_status()
        table = {"sort": {"key": "value", "dir": "desc"}, "query": "etf", "selects": {"pl": "gain"}}
        self.client.patch("/api/prefs", json={"table:overview-holdings": table}, headers=H).raise_for_status()
        got = self.client.get("/api/prefs").json()
        self.assertEqual(got["alloc_view"], "pie")
        self.assertEqual(got["theme"], "light")
        self.assertEqual(got["motion"], "reduced")
        self.assertEqual(got["table:overview-holdings"], table)
        # null forgets a key
        self.assertNotIn("theme", self.client.patch("/api/prefs", json={"theme": None}, headers=H).json())
        # unknown keys, wrong values and bad table shapes are rejected, and nothing in that request is saved
        for bad in ({"overview_range": "2W"}, {"nope": "x"}, {"table:x": {"sort": "value"}},
                    {"table:Bad Id": {"query": ""}}, {"alloc_view": "bars", "theme": "neon"},
                    {"table:x": {"query": "q" * 201}}, {"motion": "none"}):
            self.assertEqual(self.client.patch("/api/prefs", json=bad, headers=H).status_code, 422, bad)
        self.assertEqual(self.client.get("/api/prefs").json()["alloc_view"], "pie")
        for bad in ({"theme": ["dark"]}, {"overview_range": {"a": 1}}, {"table:abc\n": {"query": ""}}, {"x" * 101: None}):
            self.assertEqual(self.client.patch("/api/prefs", json=bad, headers=H).status_code, 422, bad)
        # a lone surrogate, as a browser's JSON.stringify writes it, is refused and nothing breaks
        raw = {**H, "Content-Type": "application/json"}
        self.assertEqual(self.client.patch("/api/prefs", content='{"table:x": {"query": "\\ud800"}}', headers=raw).status_code, 422)
        self.assertEqual(self.client.put("/api/ai/settings", content='{"preferences": {"notes": "a\\ud800b"}}', headers=raw).status_code, 200)
        self.assertEqual(self.client.get("/api/ai/settings").json()["preferences"]["notes"], "ab")
        self.assertEqual(self.client.get("/api/prefs").status_code, 200)
        # rows an older version wrote are ignored on read and can always be forgotten
        with db.connect() as conn:
            conn.execute("INSERT INTO ui_prefs (key, value) VALUES ('theme', '{\"mode\":\"dark\"}'), ('legacy:key', '1')")
        got = self.client.get("/api/prefs").json()
        self.assertNotIn("legacy:key", got)
        self.assertNotIn("theme", got)
        self.client.patch("/api/prefs", json={"legacy:key": None}, headers=H).raise_for_status()
        # more than 40 tables: the least recently changed are forgotten, nothing is refused
        for i in range(45):
            self.client.patch("/api/prefs", json={f"table:t{i}": {"query": str(i)}}, headers=H).raise_for_status()
        tables = [k for k in self.client.get("/api/prefs").json() if k.startswith("table:")]
        self.assertEqual(len(tables), 40)
        self.assertIn("table:t44", tables)
        self.assertEqual(self.client.patch("/api/prefs", json={"theme": "dark"}).status_code, 403)  # CSRF header
        anon = TestClient(self.client.app)
        self.assertEqual(anon.get("/api/prefs").status_code, 401)

    def test_disclaimer_acceptance_is_recorded_per_version(self):
        self.setup_account()
        self.assertEqual(self.client.get("/api/disclaimer").json(), {"accepted_version": None, "accepted_at": None})
        r = self.client.post("/api/disclaimer/accept", json={"version": "2026-11-24"}, headers=H).json()
        self.assertEqual(r["accepted_version"], "2026-11-24")
        self.assertRegex(r["accepted_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(self.client.get("/api/disclaimer").json()["accepted_version"], "2026-11-24")
        self.assertEqual(self.client.post("/api/disclaimer/accept", json={"version": "yes"}, headers=H).status_code, 422)
        self.assertEqual(self.client.post("/api/disclaimer/accept", json={"version": "2026-99-99"}, headers=H).status_code, 422)
        self.client.post("/api/disclaimer/accept", json={"version": "2027-01-15"}, headers=H).raise_for_status()
        self.assertEqual(self.client.get("/api/disclaimer").json()["accepted_version"], "2027-01-15")
        self.assertEqual([h["version"] for h in json.loads(db.get_setting("disclaimer_accepted"))], ["2026-11-24", "2027-01-15"])
        self.assertEqual(self.client.post("/api/disclaimer/accept", json={"version": "2026-11-24"}).status_code, 403)
        self.assertEqual(TestClient(self.client.app).get("/api/disclaimer").status_code, 401)

    def test_purchase_platform_lookup_and_quote(self):
        self.setup_account()
        body = {"instrument_id": "VWCE", "name": "Vanguard FTSE All-World", "asset_type": "etf", "quantity": 0,
                "currency": "EUR", "market_symbol": "VWCE.DE"}
        self.client.post("/api/instruments", json=body, headers=H).raise_for_status()
        dup = self.client.post("/api/instruments?create=1", json={**body, "name": "Overwritten?"}, headers=H)
        self.assertEqual(dup.status_code, 409)                                   # the wizard never overwrites
        self.assertEqual(self.client.get("/api/instruments").json()[0]["name"], "Vanguard FTSE All-World")
        lot = {"instrument_id": "VWCE", "buy_date": "2026-11-02", "quantity": 3, "unit_price": 130.5, "platform": "  Revolut "}
        self.client.post("/api/lots", json=lot, headers=H).raise_for_status()
        self.assertEqual(self.client.get("/api/lots").json()[0]["platform"], "Revolut")
        self.assertEqual(self.client.get("/api/platforms").json(), ["Revolut"])
        self.assertEqual(self.client.post("/api/lots", json={**lot, "platform": "x" * 61}, headers=H).status_code, 422)
        bad = json.dumps({**lot, "buy_date": "2026-11-05"})[:-1] + ', "notes": "x\\udc00y"}'
        self.assertEqual(self.client.post("/api/lots", content=bad, headers={**H, "Content-Type": "application/json"}).status_code, 422)
        self.client.post("/api/lots", json={**lot, "platform": "OTP"}, headers=H).raise_for_status()  # same buy, other broker
        self.assertEqual(self.client.post("/api/lots", json={**lot, "platform": "OTP"}, headers=H).status_code, 409)
        self.client.post("/api/lots", json={**lot, "buy_date": "2026-11-03", "platform": "revolut"}, headers=H).raise_for_status()
        self.assertEqual(self.client.get("/api/platforms").json(), ["revolut", "OTP"])  # one entry per platform, latest spelling

        quotes = [{"symbol": "VWCE.DE", "longname": "Vanguard FTSE All-World UCITS ETF", "quoteType": "ETF", "exchDisp": "XETRA"},
                  {"symbol": "^GSPC", "shortname": "S&P 500", "quoteType": "INDEX"},
                  {"symbol": "GC=F", "shortname": "Gold", "quoteType": "FUTURE"},
                  {"symbol": "MSFTZ26.CME", "shortname": "Microsoft single stock future", "quoteType": "FUTURE"},
                  {"symbol": "SMSFT=F", "shortname": "Microsoft Corp Single Stock futures", "quoteType": "FUTURE"}]
        from analyzer import market
        market._search_cache.clear()
        with mock.patch("analyzer.market._yahoo_search", return_value=quotes + ["not-a-dict"]) as search:
            found = self.client.get("/api/lookup", params={"q": "vanguard all world"}).json()
            self.client.get("/api/lookup", params={"q": "Vanguard  All World"}).raise_for_status()  # cached
        self.assertEqual(search.call_count, 1)
        self.assertEqual([(f["symbol"], f["type"]) for f in found], [("VWCE.DE", "etf"), ("GC=F", "commodity")])
        market._search_cache.clear()
        with mock.patch("analyzer.market._yahoo_search", side_effect=RuntimeError("HTTP 502")) as down:
            self.assertEqual(self.client.get("/api/lookup", params={"q": "zzz"}).status_code, 503)
            self.assertEqual(self.client.get("/api/lookup", params={"q": "zzz"}).status_code, 503)
            hu = self.client.get("/api/lookup", params={"q": "HU0000728670"}).json()  # a fund ISIN still works offline
        self.assertEqual(down.call_count, 3)  # failures are never cached
        self.assertEqual(hu[0]["symbol"], "fund:HU0000728670")
        self.assertEqual(self.client.get("/api/lookup", params={"q": "a"}).status_code, 422)
        self.assertEqual(self.client.get("/api/lookup", params={"q": "   "}).status_code, 422)

        with mock.patch("server.main.live_quote", return_value={"price": 131.2, "currency": "EUR", "time": pd.Timestamp("2026-11-24 14:00", tz="UTC"), "error": None}):
            q = self.client.get("/api/quote", params={"symbol": "VWCE.DE"}).json()
        self.assertEqual((q["price"], q["currency"], q["divisor"]), (131.2, "EUR", 1.0))
        for raw, major in (("GBp", "GBP"), ("ZAc", "ZAR"), ("GBX", "GBP"), ("ILA", "ILS")):
            with mock.patch("server.main.live_quote", return_value={"price": 7512.0, "currency": raw, "time": None, "error": None}):
                g = self.client.get("/api/quote", params={"symbol": "VUSA.L"}).json()
            self.assertEqual((g["currency"], g["quote_currency"], g["divisor"]), (major, raw, 100.0), raw)
        with mock.patch("server.main.download_fund_nav", fake_fund):
            f = self.client.get("/api/quote", params={"symbol": "fund:HU0000728670"}).json()
        self.assertEqual((f["price"], f["currency"], f["as_of"]), (3.289577, "HUF", "2026-11-20"))
        self.assertEqual(self.client.get("/api/quote", params={"symbol": "fund:nope"}).status_code, 400)

    def test_ai_data_level_needs_explicit_confirmation_for_full(self):
        self.setup_account()
        url = "/api/ai/providers/claude-login/data-level"
        self.assertEqual(self.client.post(url, json={"data_level": "full"}, headers=H).status_code, 422)
        self.assertEqual(self.client.post(url, json={"data_level": "full", "confirm_full": True}, headers=H).json()["data_level"], "full")
        listed = {p["id"]: p for p in self.client.get("/api/ai/providers").json()}
        self.assertEqual(listed["claude-login"]["data_level"], "full")
        self.client.post(url, json={"data_level": "relative"}, headers=H).raise_for_status()
        self.assertEqual(self.client.post("/api/ai/providers/nope/data-level", json={"data_level": "relative"}, headers=H).status_code, 404)
        self.assertEqual(self.client.post(url, json={"data_level": "relative"}).status_code, 403)

    def test_favicon_ico_is_the_logo(self):
        from server import main
        if not (main.STATIC_DIR / "logo.svg").is_file():
            self.skipTest("frontend not built")
        r = self.client.get("/favicon.ico")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("image/svg+xml"))
        self.assertEqual(r.content, (main.STATIC_DIR / "logo.svg").read_bytes())

    def test_oversized_request_bodies_are_refused(self):
        self.setup_account()
        big = '{"theme": "' + "x" * (2 * 1024 * 1024) + '"}'
        raw = {**H, "Content-Type": "application/json"}
        self.assertEqual(self.client.patch("/api/prefs", content=big, headers=raw).status_code, 413)
        # a chunked body (no Content-Length) is counted as it streams in
        chunks = (b"x" * 65536 for _ in range(40))
        self.assertEqual(self.client.patch("/api/prefs", content=chunks, headers=raw).status_code, 413)
        # the limit is chosen by route, not by a content type the client picks
        multi = {**H, "Content-Type": "multipart/form-data; boundary=x"}
        self.assertEqual(self.client.patch("/api/prefs", content=b"x" * (3 * 1024 * 1024), headers=multi).status_code, 413)
        # uploads get room for a 5 MB CSV; past that they are refused too
        two_mib = b"a,b\n" + b"1,2\n" * (512 * 1024)
        r = self.client.post("/api/import/holdings", files={"file": ("h.csv", two_mib, "text/csv")}, headers=H)
        self.assertNotEqual(r.status_code, 413)
        r = self.client.post("/api/import/holdings", files={"file": ("h.csv", b"x" * (7 * 1024 * 1024), "text/csv")}, headers=H)
        self.assertEqual(r.status_code, 413)
        # an AI question with a long conversation behind it is not cut off at 1 MiB (422: history is no list)
        ask = '{"question": "hi", "history": "' + "x" * (2 * 1024 * 1024) + '"}'
        self.assertEqual(self.client.post("/api/advisor/ask", content=ask, headers=raw).status_code, 422)
        big_ask = '{"question": "hi", "history": "' + "x" * (5 * 1024 * 1024) + '"}'
        r = self.client.post("/api/advisor/ask", content=big_ask, headers=raw)
        self.assertEqual(r.status_code, 413)
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])  # a 413 keeps the headers
        # an endpoint that reads no body never acts on a refused request
        self.client.post("/api/instruments?create=1", headers=H,
                         json={"instrument_id": "KEEP", "name": "Keep", "asset_type": "etf", "quantity": 1, "currency": "EUR"})
        r = self.client.request("DELETE", "/api/instruments/KEEP?confirm_purchases=0", content=b"x" * (2 * 1024 * 1024), headers=raw)
        self.assertEqual(r.status_code, 413)
        self.assertTrue(any(i["instrument_id"] == "KEEP" for i in self.client.get("/api/instruments", headers=H).json()))
        # signed out, the larger limits do not apply: no megabytes parsed before the login check
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/advisor/ask", content=ask, headers=raw).status_code, 413)
        # normal requests are untouched (signed in again)
        r = self.client.post("/api/auth/login", json={"username": "owner", "password": "correct horse 1"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.patch("/api/prefs", json={"theme": "light"}, headers=H).status_code, 200)

    def test_a_conversation_at_another_sharing_level_is_refused(self):
        self.setup_account()
        body = {"question": "And now?", "history": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                "expected_level": "full"}  # the built-in model is on relative figures
        with mock.patch.object(live.hub, "snapshot", {"rows": [], "totals": {}}), \
             mock.patch("analyzer.advisor.complete") as sent:
            r = self.client.post("/api/advisor/ask", json=body, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Start a new conversation", r.json()["detail"])
        sent.assert_not_called()

    def test_setup_takes_the_browser_currency_once(self):
        r = self.client.post("/api/auth/setup", headers=H, json={"username": "owner", "password": "correct horse 1",
                                                                 "code": auth._setup_code, "base_currency": "eur"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(db.base_currency(), "EUR")
        self.assertEqual(self.client.get("/api/settings", headers=H).json()["currencies"][0], "EUR")

    def test_setup_ignores_an_unlisted_currency(self):
        r = self.client.post("/api/auth/setup", headers=H, json={"username": "owner", "password": "correct horse 1",
                                                                 "code": auth._setup_code, "base_currency": "XYZ"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(db.base_currency(), "HUF")  # unchanged default

    def test_edit_form_never_recreates_a_deleted_holding(self):
        self.setup_account()
        body = {"instrument_id": "GONE", "name": "Gone", "asset_type": "etf", "quantity": 1, "currency": "EUR"}
        r = self.client.post("/api/instruments?update=1", json=body, headers=H)
        self.assertEqual(r.status_code, 404)
        self.assertFalse(any(i["instrument_id"] == "GONE" for i in self.client.get("/api/instruments", headers=H).json()))
        self.assertEqual(self.client.post("/api/instruments?create=1", json=body, headers=H).status_code, 200)
        self.assertEqual(self.client.post("/api/instruments?update=1", json={**body, "name": "Kept"}, headers=H).status_code, 200)
        names = {i["instrument_id"]: i["name"] for i in self.client.get("/api/instruments", headers=H).json()}
        self.assertEqual(names["GONE"], "Kept")

    def test_security_headers(self):
        r = self.client.get("/api/health")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-frame-options"], "DENY")

    def test_portfolio_flow(self):
        self.setup_account()
        csv = ("instrument_id,name,asset_type,quantity,avg_cost,currency,market_symbol,price_divisor,notes\n"
               "SEC0,iShares Semis,etf,100,,EUR,SEC0.DE,1,\n"
               "OTP-B,OTP fund,mutual_fund,1000000,2.9,HUF,fund:HU0000728670,1,\n")
        r = self.client.post("/api/import/holdings", files={"file": ("h.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=H)
        self.assertEqual(r.json(), {"imported": 2}, r.text)
        lots = "instrument_id,buy_date,quantity,unit_price,cost_huf,notes\nSEC0,2025-03-03,100,10,,\n"
        r = self.client.post("/api/import/lots", files={"file": ("l.csv", io.BytesIO(lots.encode()), "text/csv")}, headers=H)
        self.assertEqual(r.json(), {"added": 1, "skipped_duplicates": 0})
        r = self.client.post("/api/import/lots", files={"file": ("l.csv", io.BytesIO(lots.encode()), "text/csv")}, headers=H)
        self.assertEqual(r.json(), {"added": 0, "skipped_duplicates": 1})

        snap = self.client.post("/api/portfolio/refresh", headers=H).json()
        rows = {r["id"]: r for r in snap["rows"]}
        eur_huf = 336.0 / 0.9
        self.assertEqual(snap["base_currency"], "HUF")
        self.assertAlmostEqual(rows["SEC0"]["value_base"], 100 * 13.6 * eur_huf)
        self.assertAlmostEqual(rows["SEC0"]["cost_base"], 100 * 10 * (340.0 / 0.85))   # buy-date FX via USD
        self.assertAlmostEqual(rows["OTP-B"]["value_base"], 1000000 * 3.289577, places=2)
        self.assertAlmostEqual(rows["OTP-B"]["pnl_base"], 1000000 * (3.289577 - 2.9), places=2)
        self.assertEqual(rows["OTP-B"]["price_source"], "daily")
        self.assertAlmostEqual(snap["totals"]["value_base"], rows["SEC0"]["value_base"] + rows["OTP-B"]["value_base"])
        self.assertAlmostEqual(rows["SEC0"]["today_base"], 100 * (13.6 - 13.4) * eur_huf)
        self.assertAlmostEqual(snap["fx"]["EUR"], eur_huf)

        hist = self.client.get("/api/portfolio/history?range=1M").json()
        self.assertTrue(hist["points"] and hist["points"][-1]["value"] > 0)
        self.assertEqual(hist["currency"], "HUF")
        self.assertEqual(sorted(hist["included"]), ["OTP-B", "SEC0"])
        week = self.client.get("/api/portfolio/history?range=1W").json()
        self.assertEqual(week["range"], "1W")
        self.assertTrue(week["points"] and week["points"][0]["date"] >= "2026-11-16")  # pinned today - 8 days
        self.assertEqual(week["points"][-1], hist["points"][-1])            # same right edge, shorter window
        self.assertLessEqual(len(week["points"]), 9)
        self.assertEqual(self.client.get("/api/portfolio/history?range=2W").status_code, 422)
        detail = self.client.get("/api/instruments/OTP-B").json()
        self.assertEqual(detail["prices"][-1]["close"], 3.289577)

    def test_reporting_currency_switch(self):
        self.setup_account()
        body = {"instrument_id": "SEC0", "name": "Semis", "asset_type": "etf", "quantity": 0, "currency": "EUR",
                "market_symbol": "SEC0.DE"}
        self.client.post("/api/instruments", json=body, headers=H)
        # 100 units bought for 1000 EUR paid; reporting currency HUF first
        r = self.client.post("/api/lots", json={"instrument_id": "SEC0", "buy_date": "2025-03-03", "quantity": 100,
                                                "unit_price": 10, "paid_amount": 1000, "paid_currency": "EUR"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.put("/api/settings", json={"base_currency": "XYZ"}, headers=H).status_code, 400)
        r = self.client.put("/api/settings", json={"base_currency": "eur"}, headers=H)
        self.assertEqual(r.json()["base_currency"], "EUR")
        snap = self.client.get("/api/portfolio").json()
        row = snap["rows"][0]
        self.assertEqual(snap["base_currency"], "EUR")
        self.assertAlmostEqual(row["value_base"], 100 * 13.6)            # EUR holding in EUR: no conversion
        self.assertAlmostEqual(row["cost_base"], 1000.0)                  # paid in EUR
        self.assertAlmostEqual(row["pnl_pct"], 0.36)
        self.assertNotIn("EUR", snap["fx"])
        hist = self.client.get("/api/portfolio/history?range=1M").json()
        self.assertEqual(hist["currency"], "EUR")
        self.assertAlmostEqual(hist["points"][-1]["value"], 100 * 13.0)  # fake daily close 13 EUR

    def test_delete_needs_confirmation_and_lot_ownership(self):
        self.setup_account()
        body = {"instrument_id": "A", "name": "A", "asset_type": "stock", "quantity": 0, "currency": "EUR"}
        self.assertEqual(self.client.post("/api/instruments", json=body, headers=H).status_code, 200)
        self.client.post("/api/instruments", json=dict(body, instrument_id="B", name="B"), headers=H)
        r = self.client.post("/api/lots", json={"instrument_id": "A", "buy_date": "2025-01-02", "quantity": 1, "unit_price": 5}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.post("/api/lots", json={"instrument_id": "A", "buy_date": "2025-01-02", "quantity": 1, "unit_price": 5}, headers=H).status_code, 409)
        lot_id = self.client.get("/api/lots").json()[0]["lot_id"]
        self.assertEqual(self.client.delete(f"/api/lots/{lot_id}?instrument_id=B", headers=H).status_code, 404)
        r = self.client.post("/api/instruments", json=dict(body, currency="USD"), headers=H)
        self.assertEqual(r.status_code, 409)  # currency locked once purchases exist
        self.assertEqual(self.client.delete("/api/instruments/A", headers=H).status_code, 409)
        self.assertEqual(self.client.delete("/api/instruments/A?confirm_purchases=1", headers=H).status_code, 200)

    def test_sales_cannot_exceed_the_units_held_and_a_buy_in_use_stays(self):
        self.setup_account()
        body = {"instrument_id": "A", "name": "A", "asset_type": "stock", "quantity": 0, "currency": "EUR"}
        self.client.post("/api/instruments", json=body, headers=H).raise_for_status()
        trade = {"instrument_id": "A", "quantity": 3, "unit_price": 5}
        self.assertEqual(self.client.post("/api/lots", json={**trade, "buy_date": "2025-01-02"}, headers=H).status_code, 200)
        r = self.client.post("/api/lots", json={**trade, "side": "sell", "quantity": 4, "buy_date": "2025-04-03"}, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "On 2025-04-03 you held 3 units; a sale can't be larger."))
        r = self.client.post("/api/lots", json={**trade, "side": "sell", "quantity": 1, "buy_date": "2024-12-31"}, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "On 2024-12-31 you held 0 units; a sale can't be larger."))
        self.assertEqual(self.client.post("/api/lots", json={**trade, "side": "sideways", "buy_date": "2025-04-03"}, headers=H).status_code, 422)
        sale = {**trade, "side": "sell", "quantity": 2, "unit_price": 8, "buy_date": "2025-04-03"}
        self.assertEqual(self.client.post("/api/lots", json=sale, headers=H).status_code, 200)
        self.assertEqual(self.client.post("/api/lots", json=sale, headers=H).status_code, 409)     # the same sale twice
        r = self.client.post("/api/lots", json={**trade, "side": "sell", "quantity": 2, "buy_date": "2025-02-01"}, headers=H)
        self.assertEqual(r.status_code, 400)                                                       # would starve the April sale
        self.assertIn("your sale on 2025-04-03", r.json()["detail"])

        lots = {l["side"]: l["lot_id"] for l in self.client.get("/api/lots").json()}
        r = self.client.delete(f"/api/lots/{lots['buy']}?instrument_id=A", headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "A later sale uses these units. Delete the sale first."))
        self.assertEqual(self.client.delete(f"/api/lots/{lots['sell']}?instrument_id=A", headers=H).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/lots/{lots['buy']}?instrument_id=A", headers=H).status_code, 200)

    def test_csv_sales_are_refused_with_the_line_number(self):
        self.setup_account()
        body = {"instrument_id": "A", "name": "A", "asset_type": "stock", "quantity": 0, "currency": "EUR"}
        self.client.post("/api/instruments", json=body, headers=H).raise_for_status()

        def upload(text):
            return self.client.post("/api/import/lots", files={"file": ("l.csv", io.BytesIO(text.encode()), "text/csv")}, headers=H)
        head = "instrument_id,buy_date,side,quantity,unit_price\n"
        r = upload(head + "A,2025-01-02,buy,3,5\nA,2025-04-03,SELL,2,6\nA,2025-05-01,sell,5,6\n")
        self.assertEqual((r.status_code, r.json()["detail"]),
                         (400, "CSV line 4: On 2025-05-01 you held 1 unit; a sale can't be larger."))
        self.assertEqual(self.client.get("/api/lots").json(), [])                                  # all or nothing
        r = upload(head + "A,2025-01-02,hold,3,5\n")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "CSV line 2: side must be buy or sell (empty means buy)"))
        good = head + "A,2025-01-02,,3,5\nA,2025-04-03,Sell,2,6\n"                                 # empty side = buy
        self.assertEqual(upload(good).json(), {"added": 2, "skipped_duplicates": 0})
        self.assertEqual(upload(good).json(), {"added": 0, "skipped_duplicates": 2})                # a re-import is no oversale
        self.assertTrue(self.client.get("/api/templates/lots.csv").text.startswith("instrument_id,buy_date,side,quantity,"))

    def test_sales_show_realized_pl_per_sale_per_holding_and_in_the_totals(self):
        self.setup_account()
        body = {"instrument_id": "A", "name": "A", "asset_type": "stock", "quantity": 0, "currency": "EUR"}
        self.client.post("/api/instruments", json=body, headers=H).raise_for_status()
        trade = {"instrument_id": "A", "unit_price": 5}
        self.client.post("/api/lots", json={**trade, "quantity": 3, "buy_date": "2025-01-02"}, headers=H).raise_for_status()
        self.client.post("/api/lots", json={**trade, "side": "sell", "quantity": 2, "unit_price": 8, "buy_date": "2025-04-03"}, headers=H).raise_for_status()
        self.client.put("/api/settings", json={"base_currency": "HUF"}, headers=H).raise_for_status()  # fetches the FX history
        snap = self.client.get("/api/portfolio").json()
        eur_huf = 340.0 / 0.85                                                  # buy-date and sale-date FX are both this flat rate
        row = snap["rows"][0]
        self.assertEqual((row["quantity"], row["sells"], row["closed"]), (1, 1, False))
        self.assertAlmostEqual(row["realized_base"], 2 * (8 - 5) * eur_huf)
        self.assertAlmostEqual(snap["totals"]["realized_base"], row["realized_base"])
        lots = {l["side"]: l for l in self.client.get("/api/instruments/A").json()["lots"]}
        self.assertAlmostEqual(lots["sell"]["realized_base"], row["realized_base"])
        self.assertIsNone(lots["buy"]["realized_base"])
        self.client.post("/api/lots", json={**trade, "side": "sell", "quantity": 1, "buy_date": "2025-05-02"}, headers=H).raise_for_status()
        closed = self.client.get("/api/portfolio").json()["rows"][0]                # the last unit sold
        self.assertEqual((closed["closed"], closed["weight"], closed["value_base"]), (True, None, 0.0))

    def test_record_only_holdings_and_csv_files_in_any_order(self):
        self.setup_account()
        record = "This holding's units come from its holding record. Record its purchases first, then the sale."
        self.client.post("/api/instruments", json={"instrument_id": "R", "name": "R", "asset_type": "stock", "quantity": 10,
                                                   "avg_cost": 100, "currency": "EUR"}, headers=H).raise_for_status()
        sale = {"instrument_id": "R", "side": "sell", "buy_date": "2025-04-03", "quantity": 4, "unit_price": 8}
        r = self.client.post("/api/lots", json=sale, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (400, record))     # the page shows 10 units, not 0

        def upload(text):
            return self.client.post("/api/import/lots", files={"file": ("l.csv", io.BytesIO(text.encode()), "text/csv")}, headers=H)
        head = "instrument_id,buy_date,side,quantity,unit_price\n"
        r = upload(head + "R,2025-01-02,buy,1,5\nR,2025-04-03,sell,1,8\nR,2025-04-04,buy,1,5\n")
        self.assertEqual(r.status_code, 200, r.text)                              # a purchase in the file is enough
        self.client.post("/api/instruments", json={"instrument_id": "S", "name": "S", "asset_type": "stock", "quantity": 10,
                                                   "currency": "EUR"}, headers=H).raise_for_status()
        r = upload(head + "S,2025-01-02,sell,1,8\n")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "CSV line 2: " + record))

        self.client.post("/api/instruments", json={"instrument_id": "A", "name": "A", "asset_type": "stock", "quantity": 0,
                                                   "currency": "EUR"}, headers=H).raise_for_status()
        newest_first = head + "A,2025-03-01,sell,2,6\nA,2025-03-01,buy,2,5\n"    # the sale above the buy it sells
        self.assertEqual(upload(newest_first).json(), {"added": 2, "skipped_duplicates": 0})
        self.assertEqual(upload(newest_first).json(), {"added": 0, "skipped_duplicates": 2})
        self.client.post("/api/instruments", json={"instrument_id": "B", "name": "B", "asset_type": "stock", "quantity": 0,
                                                   "currency": "EUR"}, headers=H).raise_for_status()
        self.assertEqual(upload(head + "B,2025-01-02,buy,3,5\nB,2025-02-01,sell,2,6\n").status_code, 200)
        # line 3 (not line 2) is the new sale that takes the units the stored February sale needs
        r = upload(head + "B,2025-03-01,sell,1,6\nB,2025-01-15,sell,2,6\n")
        self.assertEqual((r.status_code, r.json()["detail"]),
                         (400, "CSV line 3: That would leave your sale on 2025-02-01 larger than the 1 unit you would hold then."))

    def test_zero_price_purchase_rejected(self):
        self.setup_account()
        self.client.post("/api/instruments", json={"instrument_id": "A", "name": "A", "asset_type": "stock", "quantity": 0, "currency": "EUR"}, headers=H)
        r = self.client.post("/api/lots", json={"instrument_id": "A", "buy_date": "2025-01-02", "quantity": 1, "unit_price": 0}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertIn("unit_price or paid_amount", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
