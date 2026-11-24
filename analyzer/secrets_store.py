"""API keys for AI providers, kept outside the SQLite database.

Stored in <data dir>/secrets.json with owner-only permissions, so the database backup download
never contains them, and the API never sends them back to the browser (only the last 4 characters).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from analyzer import db

_lock = threading.Lock()


def _path() -> Path:
    return db.DATA_DIR / "secrets.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def get(name: str) -> str | None:
    with _lock:
        return _load().get(name)


def put(name: str, value: str) -> None:
    with _lock:
        data = _load()
        data[name] = value
        _save(data)


def delete(name: str) -> None:
    with _lock:
        data = _load()
        if data.pop(name, None) is not None:
            _save(data)


def hint(name: str) -> str | None:
    value = get(name)
    return f"…{value[-4:]}" if value and len(value) >= 8 else ("set" if value else None)
