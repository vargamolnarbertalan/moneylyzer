"""Single-user login for LAN access: scrypt password hash, server-side sessions, CSRF header check.

First run: no user exists, so a one-time setup code is printed to the container log
(`docker logs`). Creating the account requires that code, so another device on the
network cannot claim the dashboard before the owner does.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time

from fastapi import HTTPException, Request

from analyzer import db

log = logging.getLogger("lia.auth")
COOKIE = "lia_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "lia"
SESSION_SECONDS = 30 * 24 * 3600
MIN_PASSWORD = 10
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}
_FAIL_WINDOW, _FAIL_LIMIT = 300, 5

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_setup_code: str | None = None


def init() -> None:
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                pw_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS app_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL REFERENCES app_users(username) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            """
        )
        conn.execute("DELETE FROM app_sessions WHERE expires_at < ?", (time.time(),))
    ensure_setup_code()


def ensure_setup_code() -> None:
    global _setup_code
    if user_count() == 0 and _setup_code is None:
        _setup_code = secrets.token_hex(4).upper()
        log.warning("No account yet. One-time setup code: %s (enter it on the dashboard's setup page)", _setup_code)


def user_count() -> int:
    with db.connect() as conn:
        return conn.execute("SELECT count(*) FROM app_users").fetchone()[0]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64), **_SCRYPT)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False


def _check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Use at least {MIN_PASSWORD} characters.")


def setup(username: str, password: str, code: str) -> None:
    global _setup_code
    username = username.strip()
    with _lock:
        if user_count() > 0:
            raise HTTPException(409, "An account already exists. Sign in instead.")
        if not _setup_code or not hmac.compare_digest(code.strip().upper(), _setup_code):
            raise HTTPException(403, "Wrong setup code. It is printed in the container log (docker logs).")
        if not username or len(username) > 64:
            raise HTTPException(400, "Choose a username (up to 64 characters).")
        _check_password_rules(password)
        with db.connect() as conn:
            conn.execute("INSERT INTO app_users(username, pw_hash) VALUES (?, ?)", (username, hash_password(password)))
        _setup_code = None


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(ip, []) if now - t < _FAIL_WINDOW]
        _failures[ip] = recent
        return len(recent) >= _FAIL_LIMIT


def _record_failure(ip: str) -> None:
    with _lock:
        _failures.setdefault(ip, []).append(time.time())


def login(username: str, password: str, ip: str) -> str:
    if _rate_limited(ip):
        raise HTTPException(429, "Too many failed attempts. Wait 5 minutes.")
    with db.connect() as conn:
        row = conn.execute("SELECT pw_hash FROM app_users WHERE username = ?", (username.strip(),)).fetchone()
    # Verify against a dummy hash when the user is unknown, so timing does not reveal usernames.
    stored = row["pw_hash"] if row else _dummy_hash()
    if not verify_password(password, stored) or row is None:
        _record_failure(ip)
        raise HTTPException(401, "Wrong username or password.")
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO app_sessions(token_hash, username, expires_at) VALUES (?, ?, ?)",
            (_token_hash(token), username.strip(), time.time() + SESSION_SECONDS),
        )
    return token


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_hex(16)))
    return _DUMMY[0]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute(
            "SELECT username FROM app_sessions WHERE token_hash = ? AND expires_at > ?", (_token_hash(token), time.time())
        ).fetchone()
    return row["username"] if row else None


def logout(token: str | None) -> None:
    if token:
        with db.connect() as conn:
            conn.execute("DELETE FROM app_sessions WHERE token_hash = ?", (_token_hash(token),))


def change_password(username: str, old: str, new: str, keep_token: str | None) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT pw_hash FROM app_users WHERE username = ?", (username,)).fetchone()
    if not row or not verify_password(old, row["pw_hash"]):
        raise HTTPException(403, "Current password is wrong.")
    _check_password_rules(new)
    with db.connect() as conn:
        conn.execute("UPDATE app_users SET pw_hash = ? WHERE username = ?", (hash_password(new), username))
        # Sign out every other device.
        conn.execute("DELETE FROM app_sessions WHERE username = ? AND token_hash != ?", (username, _token_hash(keep_token or "")))


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def require_user(request: Request) -> str:
    """FastAPI dependency: a valid session cookie; unsafe methods also need the CSRF header.

    The custom header cannot be sent cross-site without a CORS preflight, which this server never
    allows, so a malicious page cannot make the browser submit changes with the owner's cookie.
    """
    user = session_user(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Sign in first.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "Missing request header.")
    return user


def require_csrf(request: Request) -> None:
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "Missing request header.")
