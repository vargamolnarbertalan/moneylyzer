"""Effort for every Claude route (with a "model default"), and the providers' own model lists."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from analyzer import advisor, db, llm, research, secrets_store


class TempDb(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(db, "DATA_DIR", d), mock.patch.object(db, "DB_PATH", d / "t.sqlite3"),
                  mock.patch("analyzer.llm.WORK_DIR", d / "claude-cwd"),
                  mock.patch("analyzer.claude_install.update_if_needed")):
            p.start()
            self.addCleanup(p.stop)
        db.initialize()
        research.init_tables()
        advisor.init_tables()


class EffortTests(TempDb):
    def _claude_cmd(self, effort: str) -> list[str]:
        p = llm.Provider(id="claude-login", name="c", kind="claude_code", model="opus", effort=effort)
        done = SimpleNamespace(returncode=0, stdout=json.dumps({"result": "ok", "is_error": False}), stderr="")
        with mock.patch("analyzer.llm.find_claude_cli", return_value="claude"), \
                mock.patch("analyzer.llm.subprocess.run", return_value=done) as run:
            llm.complete(p, "SYS", [{"role": "user", "content": "q"}])
        return run.call_args.args[0]

    def test_claude_login_passes_effort_unless_model_default(self):
        cmd = self._claude_cmd("xhigh")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "xhigh")
        self.assertNotIn("--effort", self._claude_cmd("default"))

    def test_api_model_default_sends_no_effort(self):
        p = llm.Provider(id="api", name="API", kind="anthropic", model="claude-opus-5-5", effort="default")
        secrets_store.put(p.secret_name, "sk-ant-test")
        client = mock.Mock()
        client.beta.messages.create.return_value = SimpleNamespace(
            stop_reason="end_turn", model="m", content=[SimpleNamespace(type="text", text="ok", citations=[])])
        with mock.patch("anthropic.Anthropic", return_value=client):
            llm.complete(p, "SYS", [{"role": "user", "content": "q"}])
        self.assertNotIn("output_config", client.beta.messages.create.call_args.kwargs)

    def test_unknown_effort_is_refused(self):
        with self.assertRaisesRegex(llm.LLMError, "effort must be"):
            llm.validate(llm.Provider(id="x", name="x", kind="claude_code", model="opus", effort="ultra"))

    def test_claude_login_keeps_running_as_before_once_then_follows_the_owner(self):
        self.assertEqual(advisor.get_provider("claude-login").effort, "default")  # a new install
        with db.connect() as conn:  # an install from before: "high" stored, never applied
            conn.execute("UPDATE ai_providers SET effort = 'high' WHERE id = 'claude-login'")
            conn.execute("DELETE FROM ai_settings WHERE key = 'claude_login_effort_default'")
        advisor.init_tables()
        self.assertEqual(advisor.get_provider("claude-login").effort, "default")
        advisor.save_provider({"id": "claude-login", "name": "c", "kind": "claude_code", "model": "opus",
                               "effort": "high"}, None)
        advisor.init_tables()  # every start runs this; the owner's choice stays
        self.assertEqual(advisor.get_provider("claude-login").effort, "high")


class _FakeModels(BaseHTTPRequestHandler):
    seen: dict = {}
    status = 200
    payload: object = {"data": [{"id": "llama3.1:8b"}, {"id": "gpt-x"}, {"id": "bad id!"}, {"id": 5}, "junk"]}

    def do_GET(self):
        type(self).seen = {"path": self.path, "auth": self.headers.get("Authorization")}
        body = json.dumps(self.payload if self.status == 200 else {"error": {"message": "bad key"}}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ModelListTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _FakeModels)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"
        _FakeModels.status = 200

    def test_openai_compatible_list(self):
        self.assertEqual(llm.list_models("openai_compat", self.base + "/", None), {"models": ["gpt-x", "llama3.1:8b"], "total": 2})
        self.assertEqual(_FakeModels.seen, {"path": "/v1/models", "auth": None})
        llm.list_models("openai_compat", self.base, "sk-1")
        self.assertEqual(_FakeModels.seen["auth"], "Bearer sk-1")

    def test_openai_compatible_errors(self):
        _FakeModels.status = 401
        with self.assertRaisesRegex(llm.LLMError, "valid API key.*bad key"):
            llm.list_models("openai_compat", self.base, "wrong")
        with self.assertRaisesRegex(llm.LLMError, "base URL"):
            llm.list_models("openai_compat", "", None)
        with self.assertRaisesRegex(llm.LLMError, "no model list"):
            llm.list_models("claude_code", "", None)

    def test_a_key_never_goes_over_plain_http_to_the_internet(self):
        with mock.patch.object(llm._opener, "open") as opened:
            with self.assertRaisesRegex(llm.LLMError, "Use https"):
                llm.list_models("openai_compat", "http://api.openai.com/v1", "sk-1")
            with self.assertRaisesRegex(llm.LLMError, "must use https"):
                llm.list_models("anthropic", "http://proxy.example.com", "sk-ant")
        opened.assert_not_called()

    def test_a_redirect_is_not_followed_with_the_key(self):
        target = HTTPServer(("127.0.0.1", 0), _FakeModels)
        threading.Thread(target=target.serve_forever, daemon=True).start()
        self.addCleanup(target.server_close)
        self.addCleanup(target.shutdown)

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{target.server_port}/v1/models")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        hop = HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=hop.serve_forever, daemon=True).start()
        self.addCleanup(hop.server_close)
        self.addCleanup(hop.shutdown)
        _FakeModels.seen = {}
        with self.assertRaisesRegex(llm.LLMError, "redirected"):
            llm.list_models("openai_compat", f"http://127.0.0.1:{hop.server_port}/v1", "sk-SECRET")
        self.assertEqual(_FakeModels.seen, {})  # the other host never saw the request, let alone the key

    def test_bad_keys_and_broken_servers_are_readable_errors(self):
        with self.assertRaisesRegex(llm.LLMError, "paste it again"):
            llm.list_models("openai_compat", self.base, "sk-abc\ndef")
        with self.assertRaisesRegex(llm.LLMError, "not valid"):
            llm.list_models("openai_compat", "http://localhost:11434x/v1", None)
        with mock.patch.object(llm._opener, "open", side_effect=llm.http.client.RemoteDisconnected("closed")):
            with self.assertRaisesRegex(llm.LLMError, r"Bad or slow response .*\(RemoteDisconnected\)") as caught:
                llm.list_models("openai_compat", self.base, "sk-SECRET")
        self.assertNotIn("sk-SECRET", str(caught.exception))

    def test_claude_api_list_uses_the_sdk(self):
        with self.assertRaisesRegex(llm.LLMError, "API key first"):
            llm.list_models("anthropic", "", None)
        client = mock.Mock()
        client.models.list.return_value = iter([SimpleNamespace(id="claude-opus-5-5"), SimpleNamespace(id="claude-haiku-5-5"),
                                                SimpleNamespace(id="claude-opus-5-5"), SimpleNamespace(id=None), SimpleNamespace()])
        with mock.patch("anthropic.Anthropic", return_value=client) as ctor:
            self.assertEqual(llm.list_models("anthropic", "", "sk-ant"),
                             {"models": ["claude-opus-5-5", "claude-haiku-5-5"], "total": 2})
        self.assertEqual(ctor.call_args.kwargs["api_key"], "sk-ant")
        self.assertIsNone(ctor.call_args.kwargs["base_url"])


if __name__ == "__main__":
    unittest.main()
