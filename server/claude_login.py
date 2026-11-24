"""Connect Claude Code to the owner's Claude subscription from the browser (container only).

Runs `claude auth login --claudeai` in a pseudo-terminal, as verified on 2026-11-24 with
Claude Code 2.1.310 in a Linux container: it prints an authorize URL and waits at
"Paste code here if prompted >". The UI shows the URL, the owner signs in on claude.com,
copies the code shown there and pastes it into the dashboard, which types it into the CLI.
Credentials land in CLAUDE_CONFIG_DIR (on the data volume), so they survive restarts.
"""
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import threading
import time

from analyzer import claude_install
from analyzer.ai import cli_environment, find_claude_cli

URL_RE = re.compile(r"https://claude\.(?:com|ai)/[^\s\x07\x1b\]]+")
CODE_RE = re.compile(r"[A-Za-z0-9_\-#.~]{8,1024}")
SESSION_TTL = 600


class LoginError(RuntimeError):
    pass


class _Session:
    def __init__(self) -> None:
        try:
            import pty  # Linux/macOS only; imported here so the module loads on Windows for tests
        except ImportError:
            raise LoginError("Connecting Claude from the browser works in the Docker image (Linux). "
                             "On Windows, sign in once with `claude` in a terminal instead.") from None

        cli = find_claude_cli()
        if cli is None:
            try:
                cli = claude_install.install()  # first use: ~130 MB download, checksum-verified
            except Exception as exc:
                raise LoginError(f"Could not install Claude Code: {exc}") from None
        master, slave = pty.openpty()
        env = cli_environment()
        env["BROWSER"] = "true"  # no browser in the container; the URL is shown in the dashboard instead
        self.proc = subprocess.Popen(
            [cli, "auth", "login", "--claudeai"],
            stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True, start_new_session=True,
        )
        os.close(slave)
        self.master = master
        self.buffer = ""
        self.started = time.time()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.5)
                if ready:
                    chunk = os.read(self.master, 4096)
                    if not chunk:
                        break
                    self.buffer += chunk.decode("utf-8", "replace")
                elif self.proc.poll() is not None:
                    break
            except OSError:
                break

    def url(self) -> str | None:
        m = URL_RE.search(self.buffer)
        return m.group(0) if m else None

    def alive(self) -> bool:
        return self.proc.poll() is None and time.time() - self.started < SESSION_TTL

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            os.close(self.master)
        except OSError:
            pass


_lock = threading.Lock()
_session: _Session | None = None


def start() -> str:
    """Begin a login and return the authorize URL to open."""
    global _session
    with _lock:
        if _session is not None and _session.alive() and _session.url():
            return _session.url()
        if _session is not None:
            _session.close()
        _session = _Session()
        deadline = time.time() + 20
        while time.time() < deadline:
            if _session.url():
                return _session.url()
            if _session.proc.poll() is not None:
                break
            time.sleep(0.2)
        tail = _session.buffer[-300:]
        _session.close()
        _session = None
        raise LoginError(f"Claude Code did not print a sign-in link. Output: {tail!r}")


def submit(code: str) -> dict:
    """Type the code from claude.com into the waiting CLI and return the new auth status."""
    global _session
    code = code.strip()
    if not CODE_RE.fullmatch(code):
        raise LoginError("That does not look like a Claude sign-in code. Copy the whole code from the claude.com page.")
    with _lock:
        if _session is None or not _session.alive():
            raise LoginError("The sign-in expired. Click Connect again.")
        os.write(_session.master, (code + "\r").encode("ascii"))
        deadline = time.time() + 45
        while time.time() < deadline and _session.proc.poll() is None:
            time.sleep(0.3)
        output = _session.buffer
        _session.close()
        _session = None
    st = status()
    if not st.get("loggedIn"):
        text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07", "", output)
        text = URL_RE.sub("", text).split("Paste code here if prompted >")[-1]
        tail = " ".join(text.split())[-300:] or "no message"
        raise LoginError(f"Sign-in did not complete. Claude Code said: {tail}")
    return st


def status() -> dict:
    """`claude auth status --json`, reduced to what the UI needs (no tokens)."""
    cli = find_claude_cli()
    if cli is None:
        return {"available": False, "installed": False, "loggedIn": False,
                "install_note": f"Claude Code {claude_install.VERSION} (~130 MB) is downloaded from npm when you click Connect."}
    try:
        out = subprocess.run([cli, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                             env=cli_environment())
        data = json.loads(out.stdout or "{}")
    except Exception as exc:
        return {"available": True, "loggedIn": False, "error": str(exc)}
    return {
        "available": True,
        "loggedIn": bool(data.get("loggedIn")),
        "authMethod": data.get("authMethod"),
        "subscription": data.get("subscriptionType") or data.get("subscription"),
        "email": data.get("email") or data.get("emailAddress"),
    }


def logout() -> dict:
    cli = find_claude_cli()
    if cli is not None:
        subprocess.run([cli, "auth", "logout"], capture_output=True, text=True, timeout=20, env=cli_environment())
    return status()
