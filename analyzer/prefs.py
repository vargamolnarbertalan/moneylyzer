"""View preferences synced across the owner's devices (theme, sidebar, chart ranges, table sort/filters).

One row per key, so two devices saving different choices never overwrite each other. Only known
keys with known shapes are stored: the browser is the only writer, but the API is reachable by
anything holding the session, so nothing free-form lands in the database.
"""
from __future__ import annotations

import json
import re
import sqlite3

from analyzer import db

CHOICES: dict[str, frozenset[str]] = {
    "theme": frozenset({"dark", "light"}),
    "motion": frozenset({"full", "reduced"}),  # in-app animation switch (WCAG 2.2.2), beside the OS setting
    "sidebar": frozenset({"open", "collapsed"}),
    "alloc_view": frozenset({"bars", "grid", "pie"}),
    "overview_range": frozenset({"1W", "1M", "3M", "6M", "1Y", "3Y", "ALL"}),
    "holding_range": frozenset({"1W", "1M", "6M", "1Y", "ALL"}),
}
TABLE_KEY = re.compile(r"table:[a-z0-9][a-z0-9-]{0,39}")  # one per DataTable / list (useSortFilter id); fullmatch
MAX_TABLES = 40
MAX_QUERY = 200
MAX_SELECTS = 20
MAX_TOKEN = 60


class PrefsError(ValueError):
    """A key or value the app never writes; the whole request is rejected."""


def init_tables() -> None:
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ui_prefs ("
            " key TEXT PRIMARY KEY,"
            " value TEXT NOT NULL,"
            " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )


def _utf8(value: str, what: str) -> str:
    """Lone UTF-16 surrogates (JSON can carry them) would store fine and break every later read."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise PrefsError(f"{what} contains an invalid character.") from None
    return value


def _token(value, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TOKEN:
        raise PrefsError(f"{what} must be a short text.")
    return _utf8(value, what)


def _table_state(value) -> dict:
    """The useSortFilter state: {sort: {key, dir} | null, query: str, selects: {key: value}}."""
    if not isinstance(value, dict) or set(value) - {"sort", "query", "selects"}:
        raise PrefsError("A table preference has an unexpected shape.")
    sort = value.get("sort")
    if sort is not None:
        if not isinstance(sort, dict) or set(sort) != {"key", "dir"} or sort["dir"] not in ("asc", "desc"):
            raise PrefsError("A table sort must be {key, dir}.")
        sort = {"key": _token(sort["key"], "The sort column"), "dir": sort["dir"]}
    query = value.get("query", "")
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise PrefsError(f"A table filter must be text of at most {MAX_QUERY} characters.")
    _utf8(query, "A table filter")
    selects = value.get("selects", {})
    if not isinstance(selects, dict) or len(selects) > MAX_SELECTS:
        raise PrefsError("A table's select filters have an unexpected shape.")
    clean = {_token(k, "A filter name"): (_utf8(v, "A filter value") if isinstance(v, str) and len(v) <= MAX_TOKEN else None)
             for k, v in selects.items()}
    if any(v is None for v in clean.values()):
        raise PrefsError("A table's select filter value must be short text.")
    return {"sort": sort, "query": query, "selects": clean}


def validate(key: str, value):
    """The value to store for `key`, or PrefsError. None means "forget this key" (allowed for any key,
    so rows an older version wrote can always be cleaned up)."""
    if not isinstance(key, str) or len(key) > 100:
        raise PrefsError("Unknown preference.")
    if value is None:
        return None
    if key in CHOICES:
        if not isinstance(value, str) or value not in CHOICES[key]:
            raise PrefsError(f"{key} must be one of: {', '.join(sorted(CHOICES[key]))}.")
        return value
    if TABLE_KEY.fullmatch(key):
        return _table_state(value)
    raise PrefsError(f"Unknown preference: {key[:60]}")


def get_all() -> dict:
    with db.connect() as conn:
        rows = conn.execute("SELECT key, value FROM ui_prefs").fetchall()
    out = {}
    for key, raw in rows:
        try:
            out[key] = validate(key, json.loads(raw))  # drop anything this version no longer accepts
        except Exception:  # noqa: BLE001 - a bad stored row must never break reading the others
            continue
    return out


def update(changes: dict) -> dict:
    """Set or clear (value None) only the given keys, all or nothing; returns every stored preference."""
    if not isinstance(changes, dict):
        raise PrefsError("Send an object of preference: value pairs.")
    clean = {key: validate(key, value) for key, value in changes.items()}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # count and write as one step, even with two devices at once
        for key, value in clean.items():
            if value is None:
                conn.execute("DELETE FROM ui_prefs WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT INTO ui_prefs (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                    " value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                    (key, json.dumps(value, separators=(",", ":"))),
                )
        # At most MAX_TABLES remembered tables: the least recently changed ones are forgotten (an old
        # table id from a previous version must never lock new tables out).
        conn.execute(
            "DELETE FROM ui_prefs WHERE key IN (SELECT key FROM ui_prefs WHERE key LIKE 'table:%' "
            "ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?)", (MAX_TABLES,))
    return get_all()
