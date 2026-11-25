"""Claude connect status: a server that can download Claude Code on the first Connect offers Connect."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from analyzer import claude_install, db
from server import claude_login

REAL_VERIFY = claude_install._verify  # the install tests replace it; one test checks the real one


class ClaudeStatusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for p in (
            mock.patch.object(db, "DATA_DIR", self.data),
            mock.patch.object(claude_login, "find_claude_cli", return_value=None),
            mock.patch.object(claude_install.platform, "system", return_value="Linux"),
            mock.patch.object(claude_install.platform, "machine", return_value="x86_64"),
            mock.patch.object(claude_install, "_container", return_value=False),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_not_downloaded_yet_still_offers_connect(self):
        # Regression: the fresh Docker volume reported available=False, so the UI hid Connect,
        # the only thing that downloads Claude Code.
        st = claude_login.status()
        self.assertTrue(st["available"])
        self.assertIs(st["installed"], False)
        self.assertFalse(st["loggedIn"])
        self.assertIn(claude_install.VERSION, st["install_note"])
        self.assertNotIn("reason", st)

    def test_unsupported_platform_explains_why(self):
        with mock.patch.object(claude_install.platform, "system", return_value="Windows"):
            st = claude_login.status()
        self.assertFalse(st["available"])
        self.assertIn("Linux x64/arm64 only", st["reason"])
        self.assertIn("CLAUDE_CLI", st["reason"])
        with mock.patch.object(claude_install.platform, "machine", return_value="armv7l"), \
                mock.patch.object(claude_install, "_container", return_value=True):
            st = claude_login.status()
        self.assertFalse(st["available"])
        self.assertIn("pick another AI model", st["reason"])

    def test_read_only_data_folder_explains_why(self):
        (self.data / "bin").mkdir()
        probed = []

        def access(path, mode):
            probed.append((Path(path), mode))
            return False

        with mock.patch.object(claude_install.os, "access", side_effect=access):
            st = claude_login.status()
        self.assertFalse(st["available"])
        self.assertIn("not writable", st["reason"])
        self.assertEqual(probed[-1], (self.data / "bin", os.W_OK | os.X_OK))

    def test_missing_data_folder_is_created_on_install(self):
        with mock.patch.object(db, "DATA_DIR", self.data / "not" / "yet"):
            self.assertTrue(claude_login.status()["available"])

    def test_noexec_data_folder_explains_why(self):
        with mock.patch.object(claude_install.os, "statvfs", create=True, return_value=mock.Mock(f_flag=8)), \
                mock.patch.object(claude_install.os, "ST_NOEXEC", 8, create=True):
            st = claude_login.status()
        self.assertFalse(st["available"])
        self.assertIn("noexec", st["reason"])

    def test_installed_cli_reports_installed(self):
        out = mock.Mock(stdout='{"loggedIn": true, "authMethod": "claude.ai", "email": "a@b.c"}')
        with mock.patch.object(claude_login, "find_claude_cli", return_value="/data/bin/claude-x"), \
                mock.patch.object(claude_login.subprocess, "run", return_value=out):
            st = claude_login.status()
        self.assertEqual((st["available"], st["installed"], st["loggedIn"]), (True, True, True))


def _fake_npm(payload: bytes = b"#!/bin/sh\necho 2.1.310\n", *, integrity: str | None = None):
    """urlopen stand-in serving the package metadata and a tarball holding package/claude."""
    import base64
    import hashlib
    import io
    import json
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("package/claude")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    tgz = buf.getvalue()
    sri = integrity or "sha512-" + base64.b64encode(hashlib.sha512(tgz).digest()).decode()
    meta = json.dumps({"dist": {"tarball": f"{claude_install.REGISTRY}/x/-/x.tgz", "integrity": sri}}).encode()

    def urlopen(request, timeout):
        url = request.full_url
        body = tgz if url.endswith(".tgz") else meta
        resp = io.BytesIO(body)
        resp.headers = {"Content-Length": str(len(body))}
        return resp

    return urlopen, len(tgz)


class ClaudeInstallTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for p in (
            mock.patch.object(db, "DATA_DIR", self.data),
            mock.patch.object(claude_install.platform, "system", return_value="Linux"),
            mock.patch.object(claude_install.platform, "machine", return_value="x86_64"),
            mock.patch.object(claude_install, "_thread", None),
            mock.patch.object(claude_install, "_done", 0),
            mock.patch.object(claude_install, "_total", None),
            mock.patch.object(claude_install, "_error", None),
            mock.patch.object(claude_install, "_failed_at", None),
            mock.patch.object(claude_install, "_failures", 0),
            # The fake binary is a shell script: running it is checked on its own below.
            mock.patch.object(claude_install, "_verify"),
            # The CLI in use is the one in the data folder (not a `claude` on this machine's PATH).
            mock.patch("analyzer.ai.find_claude_cli", side_effect=lambda: claude_install.usable_path()),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _wait(self):
        thread = claude_install._thread
        self.assertIsNotNone(thread)
        thread.join(10)
        self.assertFalse(thread.is_alive())

    def _binary(self, name: str) -> Path:
        path = self.data / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")
        os.chmod(path, 0o755)
        return path

    def test_version_bump_keeps_older_binary_until_update_installed(self):
        old = self._binary("claude-1.0.0")
        stale = self._binary("claude-0.9.0.tgz.partial")  # left by a restart mid-download
        urlopen, size = _fake_npm()
        with mock.patch.object(claude_install.urllib.request, "urlopen", side_effect=urlopen):
            # Looking the binary up never downloads anything; the previous one keeps working.
            self.assertEqual(claude_install.usable_path(), str(old))
            self.assertIsNone(claude_install._thread)
            claude_install.update_if_needed()
            self._wait()
        self.assertEqual(claude_install.usable_path(), str(claude_install.binary_path()))
        self.assertEqual(sorted(p.name for p in (self.data / "bin").iterdir()), [claude_install.binary_path().name])
        self.assertFalse(stale.exists())
        self.assertEqual(claude_install.progress(), {"downloading": False, "done": size, "total": size, "error": None})

    def test_new_version_that_does_not_run_keeps_the_older_one(self):
        old = self._binary("claude-1.0.0")
        urlopen, _ = _fake_npm()
        with mock.patch.object(claude_install.urllib.request, "urlopen", side_effect=urlopen), \
                mock.patch.object(claude_install, "_verify", side_effect=claude_install.InstallError("does not run")):
            with self.assertRaisesRegex(claude_install.InstallError, "does not run"):
                claude_install.install()
        self.assertTrue(old.exists())
        self.assertEqual(claude_install.usable_path(), str(old))
        self.assertEqual(list((self.data / "bin").glob("*.partial")), [])

    def test_verify_runs_the_binary(self):
        ok = mock.Mock(returncode=0, stdout=f"{claude_install.VERSION} (Claude Code)\n", stderr="")
        with mock.patch.object(claude_install.subprocess, "run", return_value=ok) as run:
            REAL_VERIFY(Path("/x/claude"))
        self.assertEqual(run.call_args.args[0], [str(Path("/x/claude")), "--version"])
        bad = mock.Mock(returncode=132, stdout="", stderr="Illegal instruction")
        with mock.patch.object(claude_install.subprocess, "run", return_value=bad), \
                self.assertRaisesRegex(claude_install.InstallError, "Illegal instruction"):
            REAL_VERIFY(Path("/x/claude"))
        with mock.patch.object(claude_install.subprocess, "run", side_effect=OSError(8, "Exec format error")), \
                self.assertRaisesRegex(claude_install.InstallError, "does not run on this server"):
            REAL_VERIFY(Path("/x/claude"))

    def test_interrupted_download_is_not_called_a_checksum_mismatch(self):
        urlopen, _ = _fake_npm()

        def short(request, timeout):
            resp = urlopen(request, timeout)
            if request.full_url.endswith(".tgz"):
                resp.headers = {"Content-Length": str(len(resp.getvalue()) + 1000)}
            return resp

        with mock.patch.object(claude_install.urllib.request, "urlopen", side_effect=short):
            with self.assertRaisesRegex(claude_install.InstallError, "interrupted"):
                claude_install.install()

    def test_no_update_when_another_claude_is_in_use(self):
        self._binary("claude-1.0.0")
        with mock.patch("analyzer.ai.find_claude_cli", return_value="/usr/local/bin/claude"), \
                mock.patch.object(claude_install, "start_background") as start:
            claude_install.update_if_needed()
        start.assert_not_called()

    def test_partial_file_is_not_an_older_binary(self):
        self._binary(f"claude-{claude_install.VERSION}.partial")
        self.assertIsNone(claude_install.usable_path())

    def test_failed_update_backs_off_and_a_click_retries(self):
        self._binary("claude-1.0.0")
        urlopen, _ = _fake_npm(integrity="sha512-" + "A" * 88)
        with mock.patch.object(claude_install.urllib.request, "urlopen", side_effect=urlopen) as fake, \
                self.assertLogs("lia", "WARNING"):
            claude_install.update_if_needed()
            self._wait()
            self.assertIn("Checksum mismatch", claude_install.progress()["error"])
            calls = fake.call_count
            claude_install.update_if_needed()  # an automatic update waits after a failure
            self.assertEqual(fake.call_count, calls)
            with mock.patch.object(claude_install.time, "monotonic", return_value=claude_install._failed_at + 3601):
                claude_install.start_background()  # after the first failure: one hour
            self._wait()
            calls = fake.call_count
            with mock.patch.object(claude_install.time, "monotonic", return_value=claude_install._failed_at + 3601):
                claude_install.start_background()  # after the second: two hours
            self.assertEqual(fake.call_count, calls)
            claude_install.start_background(retry=True)  # a click on Connect retries at once
            self._wait()
            self.assertGreater(fake.call_count, calls)
        self.assertIsNone(claude_install.installed_path())
        self.assertEqual(list((self.data / "bin").glob("*.partial")), [])

    def test_only_one_download_at_a_time(self):
        urlopen, _ = _fake_npm()
        gate = threading.Event()

        def slow(request, timeout):
            gate.wait(5)
            return urlopen(request, timeout)

        with mock.patch.object(claude_install.urllib.request, "urlopen", side_effect=slow) as fake:
            callers = [threading.Thread(target=claude_install.start_background, kwargs={"retry": True}) for _ in range(8)]
            for t in callers:
                t.start()
            for t in callers:
                t.join(5)
            self.assertTrue(claude_install.progress()["downloading"])
            gate.set()
            self._wait()
        self.assertEqual(fake.call_count, 2)  # one metadata request, one tarball
        self.assertIsNotNone(claude_install.installed_path())

    def test_download_deadline(self):
        urlopen, _ = _fake_npm()
        with mock.patch.object(claude_install.urllib.request, "urlopen", side_effect=urlopen), \
                mock.patch.object(claude_install, "DOWNLOAD_DEADLINE", -1):
            with self.assertRaisesRegex(claude_install.InstallError, "took more than"):
                claude_install.install()
        self.assertIsNone(claude_install.installed_path())
        self.assertEqual(list((self.data / "bin").glob("*.partial")), [])

    def test_connect_before_install_starts_background_download(self):
        with mock.patch.object(claude_login, "find_claude_cli", return_value=None), \
                mock.patch.object(claude_install, "start_background") as start:
            self.assertIsNone(claude_login.start())
            start.assert_called_once_with(retry=True)
            st = claude_login.status()
        self.assertEqual(set(st["download"]), {"downloading", "done", "total", "error"})

    def test_connect_where_install_cannot_work_is_refused(self):
        with mock.patch.object(claude_login, "find_claude_cli", return_value=None), \
                mock.patch.object(claude_install.platform, "machine", return_value="armv7l"), \
                mock.patch.object(claude_install, "start_background") as start:
            with self.assertRaisesRegex(claude_login.LoginError, "Linux x64/arm64 only"):
                claude_login.start()
        start.assert_not_called()

    def test_update_only_while_signed_in(self):
        for logged_in, expected in ((False, 0), (True, 1)):
            out = mock.Mock(stdout='{"loggedIn": %s}' % ("true" if logged_in else "false"))
            with mock.patch.object(claude_login, "find_claude_cli", return_value="/data/bin/claude-1.0.0"), \
                    mock.patch.object(claude_login.subprocess, "run", return_value=out), \
                    mock.patch.object(claude_install, "update_if_needed") as update:
                claude_login.status()
            self.assertEqual(update.call_count, expected)


if __name__ == "__main__":
    unittest.main()
