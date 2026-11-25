"""Install the Claude Code CLI on first use, into the data volume (keeps the Docker image ~300 MB smaller).

Downloads the pinned version's platform package from the npm registry, verifies its SHA-512
`integrity` from the registry metadata before anything is extracted, keeps only the `claude`
binary, and moves it into place atomically. Only the users who connect their Claude
subscription ever download it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import tarfile
import threading
import urllib.request
from pathlib import Path

from analyzer import db

VERSION = os.environ.get("CLAUDE_CODE_VERSION", "2.1.310")
REGISTRY = "https://registry.npmjs.org"
_lock = threading.Lock()


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


def install() -> str:
    """Download, verify and install; returns the binary path. Safe to call concurrently."""
    with _lock:
        existing = installed_path()
        if existing:
            return existing
        package = _package()
        meta_url = f"{REGISTRY}/{package}/{VERSION}"
        with urllib.request.urlopen(urllib.request.Request(meta_url, headers={"User-Agent": "moneylyzer"}), timeout=30) as resp:
            meta = json.load(resp)
        tarball, integrity = meta["dist"]["tarball"], meta["dist"].get("integrity", "")
        if not tarball.startswith(f"{REGISTRY}/") or not integrity.startswith("sha512-"):
            raise InstallError("Unexpected npm registry metadata; not installing.")
        expected = base64.b64decode(integrity.removeprefix("sha512-"))

        target = binary_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        archive = target.with_name(target.name + ".tgz.partial")
        staged = target.with_name(target.name + ".partial")
        digest = hashlib.sha512()
        try:
            with urllib.request.urlopen(urllib.request.Request(tarball, headers={"User-Agent": "moneylyzer"}), timeout=60) as resp, \
                    open(archive, "wb") as out:
                while chunk := resp.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
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
            os.replace(staged, target)
        finally:
            for leftover in (archive, staged):
                leftover.unlink(missing_ok=True)
        # Remove binaries of other (older) versions.
        for old in target.parent.glob("claude-*"):
            if old != target and not old.name.endswith(".partial"):
                old.unlink(missing_ok=True)
        return str(target)
