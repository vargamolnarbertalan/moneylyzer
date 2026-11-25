"""Claude connect status: a server that can download Claude Code on the first Connect offers Connect."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer import claude_install, db
from server import claude_login


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


if __name__ == "__main__":
    unittest.main()
