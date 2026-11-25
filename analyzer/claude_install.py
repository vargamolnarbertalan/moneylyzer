"""Install the Claude Code CLI on first use, into the data volume (keeps the Docker image ~300 MB smaller).

Downloads the pinned version's platform package from the npm registry, verifies its SHA-512
`integrity` from the registry metadata before anything is extracted, keeps only the `claude`
binary, and moves it into place atomically. Only the users who connect their Claude
subscription ever download it.

The download runs in a background thread (`start_background`), so no HTTP request waits on it
(a reverse proxy would cut a slow one off); the UI polls `progress()`. After an image bump to
a new pinned version, `usable_path` keeps the previous binary working while the new one downloads.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import subprocess
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

from analyzer import db

VERSION = os.environ.get("CLAUDE_CODE_VERSION", "2.1.310")
REGISTRY = "https://registry.npmjs.org"
DOWNLOAD_DEADLINE = 15 * 60  # seconds for the whole download; urllib's timeout is per read only
RETRY_AFTER = 60 * 60  # a failed automatic update waits this long (doubling); a click on Connect retries at once
log = logging.getLogger("lia")
_lock = threading.Lock()
_state_lock = threading.Lock()
_thread: threading.Thread | None = None
_done = 0
_total: int | None = None
_error: str | None = None
_failed_at: float | None = None
_failures = 0


class InstallError(RuntimeError):
    pass


def _package() -> str:
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if platform.system() != "Linux" or arch is None:
        raise InstallError(f"Automatic install supports Linux x64/arm64 only (this is {platform.system()} {platform.machine()}).")
    musl = Path("/etc/alpine-release").exists()
    return f"@anthropic-ai/claude-code-linux-{arch}{'-musl' if musl else ''}"


def _container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def install_blocker() -> str | None:
    """Why this server cannot install Claude Code on first use, or None when Connect can install it."""
    try:
        _package()
    except InstallError as exc:
        if _container():
            return f"{exc} Claude Code has no build for this server's CPU, so pick another AI model."
        return (f"{exc} Install Claude Code on this computer and sign in once by running claude in a terminal; "
                "the app then finds it on the PATH or at the path in CLAUDE_CLI.")
    probe = binary_path().parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent  # install() creates the missing folders
    if not os.access(probe, os.W_OK | os.X_OK):
        return f"The data folder {probe} is not writable, so Claude Code cannot be installed into it."
    # A noexec mount passes the access check, but the downloaded binary could never run from it.
    if hasattr(os, "statvfs") and os.statvfs(probe).f_flag & getattr(os, "ST_NOEXEC", 0):
        return f"The data folder {probe} is mounted noexec, so Claude Code cannot run from it. Mount it without noexec."
    return None


def binary_path() -> Path:
    return db.DATA_DIR / "bin" / f"claude-{VERSION}"


def installed_path() -> str | None:
    path = binary_path()
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def _older_binary() -> Path | None:
    folder = binary_path().parent
    if not folder.is_dir():
        return None
    older = []
    for p in folder.glob("claude-*"):
        if p == binary_path() or p.name.endswith(".partial"):
            continue
        try:  # the install thread may delete it meanwhile
            if p.is_file() and os.access(p, os.X_OK):
                older.append((p.stat().st_mtime, p))
        except OSError:
            continue
    return max(older)[1] if older else None


def usable_path() -> str | None:
    """The pinned binary; after a version bump, the previous one until the pinned one is installed."""
    current = installed_path()
    if current:
        return current
    older = _older_binary()
    return str(older) if older else None


def update_if_needed(*, retry: bool = False) -> None:
    """After a version bump, download the pinned version in the background.

    Only called while Claude is in use (signed in, answering, or Connect clicked), so someone who
    stopped using Claude never downloads updates; and only when the older binary here is the one
    in use (not a Claude Code on the PATH or in CLAUDE_CLI).
    """
    if installed_path() is not None:
        return
    older = _older_binary()
    if older is None:
        return
    from analyzer.ai import find_claude_cli  # imports this module; deferred to avoid a cycle
    if find_claude_cli() == str(older):
        start_background(retry=retry)


def progress() -> dict:
    """The background download, for the UI: running, bytes so far and in total, last error."""
    with _state_lock:
        return {"downloading": _thread is not None and _thread.is_alive(), "done": _done, "total": _total,
                "error": _error}


def start_background(*, retry: bool = False) -> None:
    """Start installing in a background thread unless installed or already running.

    `retry` (a click on Connect) also retries right after a failure; automatic updates back off
    (RETRY_AFTER, doubling per failure, at most a day).
    """
    global _thread, _done, _total, _error, _failed_at
    with _state_lock:
        if (_thread is not None and _thread.is_alive()) or installed_path():
            return
        if not retry and _failed_at is not None:
            wait = min(RETRY_AFTER * 2 ** max(_failures - 1, 0), 24 * 60 * 60)
            if time.monotonic() - _failed_at < wait:
                return
        _done, _total, _error = 0, None, None
        thread = threading.Thread(target=_install_in_background, name="claude-install", daemon=True)
        try:
            thread.start()
        except RuntimeError as exc:  # e.g. a pids limit
            log.warning("could not start the Claude Code download: %s", exc)
            _error, _failed_at = f"Could not start the Claude Code download: {exc}", time.monotonic()
            return
        _thread = thread


def _install_in_background() -> None:
    global _error, _failed_at, _failures
    try:
        install()
    except Exception as exc:  # reported to the UI through progress()
        log.warning("Claude Code %s install failed: %s", VERSION, exc)
        with _state_lock:
            _error = f"Could not install Claude Code: {exc}"
            _failed_at = time.monotonic()
            _failures += 1
    else:
        with _state_lock:
            _failures = 0


def _report(done: int, total: int | None) -> None:
    global _done, _total
    with _state_lock:
        _done, _total = done, total


def install() -> str:
    """Download, verify and install; returns the binary path. Safe to call concurrently."""
    with _lock:
        existing = installed_path()
        if existing:
            return existing
        package = _package()
        started = time.monotonic()
        meta_url = f"{REGISTRY}/{package}/{VERSION}"
        with urllib.request.urlopen(urllib.request.Request(meta_url, headers={"User-Agent": "moneylyzer"}), timeout=30) as resp:
            meta = json.load(resp)
        tarball, integrity = meta["dist"]["tarball"], meta["dist"].get("integrity", "")
        if not tarball.startswith(f"{REGISTRY}/") or not integrity.startswith("sha512-"):
            raise InstallError("Unexpected npm registry metadata; not installing.")
        expected = base64.b64decode(integrity.removeprefix("sha512-"))

        target = binary_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        # A restart mid-download kills the thread before its cleanup; we hold the lock, so these are stale.
        _remove(p for p in target.parent.glob("claude-*") if p.name.endswith(".partial"))
        archive = target.with_name(target.name + ".tgz.partial")
        staged = target.with_name(target.name + ".partial")
        digest = hashlib.sha512()
        try:
            with urllib.request.urlopen(urllib.request.Request(tarball, headers={"User-Agent": "moneylyzer"}), timeout=60) as resp, \
                    open(archive, "wb") as out:
                length = resp.headers.get("Content-Length")
                total = int(length) if length and length.isdigit() else None
                done = 0
                _report(done, total)
                # read1 returns whatever arrived (read(n) would block until n bytes), so the deadline
                # holds within one socket timeout even on a link that trickles.
                while chunk := resp.read1(64 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
                    done += len(chunk)
                    _report(done, total)
                    if time.monotonic() - started > DOWNLOAD_DEADLINE:
                        raise InstallError(f"The download took more than {DOWNLOAD_DEADLINE // 60} minutes; try again later.")
            if total is not None and done != total:  # read1 returns b"" when the server hangs up early
                raise InstallError("The download was interrupted; try again.")
            if digest.digest() != expected:
                raise InstallError("Checksum mismatch for the Claude Code download; not installing.")
            with tarfile.open(archive, "r:gz") as tar:
                member = tar.getmember("package/claude")
                if not member.isfile():
                    raise InstallError("The Claude Code package has an unexpected layout.")
                source = tar.extractfile(member)
                with open(staged, "wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
            os.chmod(staged, 0o755)
            _verify(staged)  # before it replaces a working older version
            os.replace(staged, target)
        finally:
            _remove((archive, staged))
        # Installed: remove other versions. A file that cannot be removed does not undo the install.
        _remove(p for p in target.parent.glob("claude-*") if p != target)
        return str(target)


def _verify(binary: Path) -> None:
    """The checksum proves what npm served, not that this host can run it (CPU features, C library)."""
    try:
        done = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"Claude Code {VERSION} does not run on this server: {exc}") from None
    if done.returncode != 0 or VERSION not in done.stdout:
        raise InstallError(f"Claude Code {VERSION} does not run on this server: {(done.stderr or done.stdout).strip()[:200]}")


def _remove(paths) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not delete %s: %s", path, exc)
