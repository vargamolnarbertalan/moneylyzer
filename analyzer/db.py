from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from analyzer.engine import fifo_shortfall, shortfall_message, units_text

ROOT = Path(__file__).resolve().parents[1]
# LIA_DATA_DIR lets the container keep the database on a mounted volume.
DATA_DIR = Path(os.environ.get("LIA_DATA_DIR") or ROOT / "data")
DB_PATH = DATA_DIR / "investment_analyzer.sqlite3"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, and always close (an open handle locks the file on Windows)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    finally:
        conn.close()


ASSET_TYPE_CODES = ("stock", "etf", "mutual_fund", "government_bond", "commodity")
INSTRUMENT_COLUMNS = (
    "instrument_id, name, asset_type, quantity, avg_cost, currency, "
    "market_symbol, price_divisor, notes, updated_at"
)


def _instruments_table_sql(table: str) -> str:
    allowed = ",".join(f"'{code}'" for code in ASSET_TYPE_CODES)
    return f"""
            CREATE TABLE IF NOT EXISTS {table} (
                instrument_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                asset_type TEXT NOT NULL CHECK(asset_type IN ({allowed})),
                quantity REAL NOT NULL DEFAULT 0,
                avg_cost REAL,
                currency TEXT NOT NULL DEFAULT 'EUR',
                market_symbol TEXT,
                price_divisor REAL NOT NULL DEFAULT 1 CHECK(price_divisor > 0),
                notes TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
    """


_MIGRATION_LOCK = threading.Lock()  # Streamlit sessions (browser tabs) run as threads of one process


def _needs_asset_type_migration(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='instruments'").fetchone()
    return row is not None and not all(f"'{code}'" in row["sql"] for code in ASSET_TYPE_CODES)


def _migrate_asset_types(conn: sqlite3.Connection) -> None:
    """Rebuild an instruments table whose CHECK constraint lacks newer asset types.

    SQLite cannot alter a CHECK constraint, so this follows SQLite's documented table-rebuild
    procedure, with foreign keys off during the swap so prices are neither deleted nor orphaned.
    Safety: the schema is re-checked under a write lock (BEGIN IMMEDIATE), so concurrent starts
    migrate once; the backup is copied while that lock blocks all writers, to a temp name that is
    renamed only when complete; any failure rolls back and leaves the database unchanged.
    """
    if not _needs_asset_type_migration(conn):
        return
    with _MIGRATION_LOCK:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")  # only effective outside a transaction
        backup: Path | None = None
        committed = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not _needs_asset_type_migration(conn):  # another session finished first
                conn.execute("ROLLBACK")
                return
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = DB_PATH.with_name(f"{DB_PATH.stem}.before-migration-{stamp}.sqlite3")
            partial = backup.with_suffix(".partial")
            shutil.copy2(DB_PATH, partial)
            os.replace(partial, backup)
            violations_before = set(map(tuple, conn.execute("PRAGMA foreign_key_check").fetchall()))
            conn.execute("DROP TABLE IF EXISTS instruments_new")
            conn.execute(_instruments_table_sql("instruments_new"))
            conn.execute(f"INSERT INTO instruments_new ({INSTRUMENT_COLUMNS}) SELECT {INSTRUMENT_COLUMNS} FROM instruments")
            conn.execute("DROP TABLE instruments")
            conn.execute("ALTER TABLE instruments_new RENAME TO instruments")
            # Pre-existing orphan rows are tolerated; the rebuild must not create new ones.
            if set(map(tuple, conn.execute("PRAGMA foreign_key_check").fetchall())) - violations_before:
                raise sqlite3.IntegrityError(f"asset-type migration would orphan rows; nothing changed (backup: {backup.name})")
            conn.execute("COMMIT")
            committed = True
        finally:
            if conn.in_transaction:  # any exception, including KeyboardInterrupt / Streamlit stop
                conn.execute("ROLLBACK")
            conn.execute("PRAGMA foreign_keys = ON")
            # A failed attempt left the database unchanged: drop its copy so retries don't pile up.
            if backup is not None and not committed:
                backup.unlink(missing_ok=True)


def initialize() -> None:
    with connect() as conn:
        _migrate_asset_types(conn)
        conn.executescript(
            _instruments_table_sql("instruments")
            + """
            CREATE TABLE IF NOT EXISTS prices (
                instrument_id TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL NOT NULL CHECK(close > 0),
                currency TEXT,
                source TEXT NOT NULL DEFAULT 'CSV import',
                PRIMARY KEY (instrument_id, date),
                FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id) ON DELETE CASCADE
            );

            DROP INDEX IF EXISTS idx_prices_instrument_date;  -- duplicated the primary key

            -- One row per transaction (a buy or a sell; the table keeps its old name). buy_date is the
            -- trade date. unit_price is in the instrument's quote currency and price convention
            -- (price_divisor applies). paid_amount/paid_currency is what was actually paid (fees
            -- included) for a buy, or received (net of fees) for a sell, when known; otherwise the
            -- amount in the reporting currency is derived from fx_usd rates on the trade date.
            -- quantity stays positive: the side says which way it goes.
            CREATE TABLE IF NOT EXISTS lots (
                lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id TEXT NOT NULL,
                buy_date TEXT NOT NULL,
                quantity REAL NOT NULL CHECK(quantity > 0),
                unit_price REAL NOT NULL CHECK(unit_price >= 0),
                paid_amount REAL CHECK(paid_amount IS NULL OR paid_amount > 0),
                paid_currency TEXT,
                notes TEXT,
                platform TEXT,  -- broker / app / bank the transaction was made at (free text)
                side TEXT NOT NULL DEFAULT 'buy' CHECK(side IN ('buy','sell')),
                FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_lots_instrument ON lots(instrument_id);

            -- Daily FX: units of `currency` per 1 USD (USD itself is 1 and not stored).
            -- Any pair A->B on a date is per_usd[B] / per_usd[A].
            CREATE TABLE IF NOT EXISTS fx_usd (
                currency TEXT NOT NULL,
                date TEXT NOT NULL,
                per_usd REAL NOT NULL CHECK(per_usd > 0),
                source TEXT NOT NULL,
                PRIMARY KEY (currency, date)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Earlier builds cached HUF-based rates here; fx_usd replaces them (cache only, safe to drop).
            DROP TABLE IF EXISTS fx_rates;
            """
        )
        _migrate_lot_payment(conn)
        _migrate_lot_platform(conn)
        _migrate_lot_side(conn)


def _migrate_lot_payment(conn: sqlite3.Connection) -> None:
    """Lots from earlier builds stored `cost_huf`; carry it over as paid_amount in HUF (column kept, unused)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(lots)")}
    if "paid_amount" not in cols:
        conn.execute("ALTER TABLE lots ADD COLUMN paid_amount REAL CHECK(paid_amount IS NULL OR paid_amount > 0)")
        conn.execute("ALTER TABLE lots ADD COLUMN paid_currency TEXT")
        if "cost_huf" in cols:
            conn.execute("UPDATE lots SET paid_amount = cost_huf, paid_currency = 'HUF' WHERE cost_huf > 0")


def _migrate_lot_platform(conn: sqlite3.Connection) -> None:
    """Purchases gained a platform (Revolut, OTP, a bank...) in Nov 2026; older rows keep NULL."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(lots)")}
    if "platform" not in cols:
        try:
            conn.execute("ALTER TABLE lots ADD COLUMN platform TEXT")
        except sqlite3.OperationalError as exc:  # another process added it a moment ago
            if "duplicate column" not in str(exc).lower():
                raise


def _migrate_lot_side(conn: sqlite3.Connection) -> None:
    """Transactions can be sells since Nov 2026; every older row was a purchase, which the default says."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(lots)")}
    if "side" not in cols:
        try:
            conn.execute("ALTER TABLE lots ADD COLUMN side TEXT NOT NULL DEFAULT 'buy' CHECK(side IN ('buy','sell'))")
        except sqlite3.OperationalError as exc:  # another process added it a moment ago
            if "duplicate column" not in str(exc).lower():
                raise


DEFAULT_BASE_CURRENCY = "HUF"


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO app_settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def base_currency() -> str:
    return get_setting("base_currency", DEFAULT_BASE_CURRENCY) or DEFAULT_BASE_CURRENCY


def upsert_instrument(row: dict) -> None:
    sql = """
        INSERT INTO instruments (
            instrument_id, name, asset_type, quantity, avg_cost, currency,
            market_symbol, price_divisor, notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(instrument_id) DO UPDATE SET
            name=excluded.name,
            asset_type=excluded.asset_type,
            quantity=excluded.quantity,
            avg_cost=excluded.avg_cost,
            currency=excluded.currency,
            market_symbol=excluded.market_symbol,
            price_divisor=excluded.price_divisor,
            notes=excluded.notes,
            updated_at=CURRENT_TIMESTAMP
    """
    values = (
        str(row["instrument_id"]).strip(),
        str(row["name"]).strip(),
        str(row["asset_type"]).strip(),
        float(row.get("quantity", 0) or 0),
        None if pd.isna(row.get("avg_cost")) or row.get("avg_cost") in (None, "") else float(row["avg_cost"]),
        str(row.get("currency", "EUR") or "EUR").strip().upper(),
        None if pd.isna(row.get("market_symbol")) or row.get("market_symbol") in (None, "") else str(row["market_symbol"]).strip(),
        float(row.get("price_divisor", 1) or 1),
        None if pd.isna(row.get("notes")) else str(row.get("notes", "")),
    )
    with connect() as conn:
        conn.execute(sql, values)


def upsert_instruments(rows: Iterable[dict]) -> int:
    count = 0
    for row in rows:
        upsert_instrument(row)
        count += 1
    return count


def get_instruments() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT * FROM instruments ORDER BY asset_type, name", conn
        )


def delete_instrument(instrument_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM instruments WHERE instrument_id = ?", (instrument_id,))


def save_prices(instrument_id: str, prices: pd.DataFrame, source: str) -> int:
    if prices.empty:
        return 0
    records = []
    for row in prices.itertuples(index=False):
        dt = pd.to_datetime(row.date).date().isoformat()
        close = float(row.close)
        if pd.isna(close) or close <= 0:
            continue
        currency = getattr(row, "currency", None)
        if pd.isna(currency):
            currency = None
        records.append((instrument_id, dt, close, currency, source))
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO prices(instrument_id, date, close, currency, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, date) DO UPDATE SET
                close=excluded.close,
                currency=COALESCE(excluded.currency, prices.currency),
                source=excluded.source
            """,
            records,
        )
    return len(records)


def _price_frame(df: pd.DataFrame) -> pd.DataFrame:
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")  # stored as ISO dates: fast path
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).drop_duplicates("date").sort_values("date")
    return df


def get_prices(instrument_id: str, since: str | None = None) -> pd.DataFrame:
    """Daily prices, oldest first; `since` (ISO date) limits the read to recent rows."""
    sql = "SELECT date, close, currency, source FROM prices WHERE instrument_id=?"
    params: tuple = (instrument_id,)
    if since:
        sql += " AND date >= ?"
        params += (since,)
    with connect() as conn:
        df = pd.read_sql_query(sql + " ORDER BY date", conn, params=params)
    return _price_frame(df)


def get_recent_prices(instrument_id: str, n: int) -> pd.DataFrame:
    """The last n daily prices, oldest first (cheap: reads n rows through the primary key)."""
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, close, currency, source FROM prices WHERE instrument_id=? ORDER BY date DESC LIMIT ?",
            conn, params=(instrument_id, int(n)),
        )
    return _price_frame(df)


def get_all_prices(since: str | None = None) -> pd.DataFrame:
    """All instruments' daily prices in one query (instrument_id, date, close, currency).

    With `since`, each instrument's last price before it is included too, so a holding priced
    rarely (a bond, a hand-imported fund) still has a value to carry into a short window.
    """
    sql = "SELECT instrument_id, date, close, currency FROM prices"
    params: tuple = ()
    if since:
        sql += (" WHERE date >= ?"
                " UNION ALL SELECT p.instrument_id, p.date, p.close, p.currency FROM prices p"
                " JOIN (SELECT instrument_id, MAX(date) AS d FROM prices WHERE date < ? GROUP BY instrument_id) last"
                " ON p.instrument_id = last.instrument_id AND p.date = last.d")
        params = (since, since)
    with connect() as conn:
        df = pd.read_sql_query(sql + " ORDER BY instrument_id, date", conn, params=params)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    return df


def backup_bytes() -> bytes:
    """Consistent snapshot of the database (SQLite backup API), safe while other sessions write."""
    if not DB_PATH.exists():
        initialize()
    memory = sqlite3.connect(":memory:")
    try:
        with connect() as conn:
            conn.backup(memory)
        return memory.serialize()
    finally:
        memory.close()


def add_lot(lot: dict) -> None:
    add_lots([lot])


def get_lots(instrument_id: str | None = None) -> pd.DataFrame:
    sql = "SELECT lot_id, instrument_id, buy_date, side, quantity, unit_price, paid_amount, paid_currency, notes, platform FROM lots"
    params: tuple = ()
    if instrument_id is not None:
        sql += " WHERE instrument_id = ?"
        params = (instrument_id,)
    with connect() as conn:
        return pd.read_sql_query(sql + " ORDER BY instrument_id, buy_date, lot_id", conn, params=params)


class SaleTooLarge(ValueError):
    """A sale would be larger than the units held on its date, or would leave a later sale so.

    `row` is the index, in the list given to add_lots, of the sale to blame (for a CSV line number)."""

    def __init__(self, message: str, row: int):
        super().__init__(message)
        self.row = row


class LotInUse(ValueError):
    """A buy cannot be deleted while a later sale needs its units."""


def delete_lot(lot_id: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT instrument_id, side FROM lots WHERE lot_id = ?", (int(lot_id),)).fetchone()
        if row is not None and row["side"] == "buy":
            rest = conn.execute("SELECT lot_id, buy_date, side, quantity FROM lots WHERE instrument_id = ? AND lot_id != ?",
                                (row["instrument_id"], int(lot_id))).fetchall()
            if fifo_shortfall([dict(r) for r in rest]):
                raise LotInUse("A later sale uses these units. Delete the sale first.")
        conn.execute("DELETE FROM lots WHERE lot_id = ?", (int(lot_id),))


def save_fx_usd(currency: str, rates: pd.DataFrame, source: str) -> int:
    """rates: columns date, per_usd (units of currency per 1 USD)."""
    records = [
        (currency, pd.to_datetime(r.date).date().isoformat(), float(r.per_usd), source)
        for r in rates.itertuples(index=False)
        if not pd.isna(r.per_usd) and float(r.per_usd) > 0
    ]
    with connect() as conn:
        conn.executemany(
            "INSERT INTO fx_usd(currency, date, per_usd, source) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(currency, date) DO UPDATE SET per_usd=excluded.per_usd, source=excluded.source",
            records,
        )
    return len(records)


def fx_usd_on_or_before(currency: str, date: str) -> tuple[float, str] | None:
    """Latest stored units-per-USD for currency on or before date (ISO), as (per_usd, date). USD is 1."""
    if currency == "USD":
        return 1.0, date
    with connect() as conn:
        row = conn.execute(
            "SELECT per_usd, date FROM fx_usd WHERE currency = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (currency, date),
        ).fetchone()
    return (float(row["per_usd"]), row["date"]) if row else None


def fx_usd_series(currency: str) -> pd.Series:
    if currency == "USD":
        return pd.Series(dtype=float)
    with connect() as conn:
        rows = conn.execute("SELECT date, per_usd FROM fx_usd WHERE currency = ? ORDER BY date", (currency,)).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series([float(r["per_usd"]) for r in rows], index=pd.to_datetime([r["date"] for r in rows]))


def _lot_values(lot: dict) -> tuple:
    raw = lot.get("paid_amount")
    paid = None if raw is None or pd.isna(raw) or float(raw) <= 0 else float(raw)
    side = str(lot.get("side") or "buy").strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    return (
        str(lot["instrument_id"]).strip(),
        pd.to_datetime(lot["buy_date"]).date().isoformat(),
        float(lot["quantity"]),
        float(lot["unit_price"]),
        paid,
        (str(lot.get("paid_currency") or "").strip().upper() or None) if paid is not None else None,
        lot.get("notes") or None,
        (str(lot.get("platform") or "").strip()[:60] or None),
        side,
    )


def add_lots(lots: list[dict]) -> tuple[int, int]:
    """Insert transactions in one transaction; exact duplicates of stored lots are skipped.

    Returns (added, skipped). A failure inserts nothing, so a retried import never half-duplicates.
    A sale larger than the units held on its date (FIFO replay of everything stored plus the new rows,
    checked after the duplicates are dropped) raises SaleTooLarge and inserts nothing.
    """
    added = skipped = 0
    with connect() as conn:
        new_ids: dict[int, int] = {}      # lot_id of each inserted row -> its index in `lots`
        first_sale: dict[str, int] = {}   # instrument_id -> index of its first inserted sale
        for index, lot in enumerate(lots):
            values = _lot_values(lot)
            duplicate = conn.execute(
                "SELECT 1 FROM lots WHERE instrument_id=? AND buy_date=? AND quantity=? AND unit_price=? "
                "AND paid_amount IS ? AND paid_currency IS ? AND platform IS ? AND side=? LIMIT 1",
                (*values[:6], values[7], values[8]),
            ).fetchone()
            if duplicate:
                skipped += 1
                continue
            cursor = conn.execute(
                "INSERT INTO lots(instrument_id, buy_date, quantity, unit_price, paid_amount, paid_currency, notes, platform, side) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            new_ids[cursor.lastrowid] = index
            if values[8] == "sell":
                first_sale.setdefault(values[0], index)
            added += 1
        # Only a new sale can break the replay (an extra buy never does).
        for instrument_id, index in first_sale.items():
            stored = conn.execute("SELECT lot_id, buy_date, side, quantity FROM lots WHERE instrument_id = ?",
                                  (instrument_id,)).fetchall()
            problem = fifo_shortfall([dict(r) for r in stored])
            if problem is None:
                continue
            if problem["lot"]["lot_id"] in new_ids:
                raise SaleTooLarge(shortfall_message(problem), new_ids[problem["lot"]["lot_id"]])
            raise SaleTooLarge(f"That would leave your sale on {problem['date']} larger than the "
                               f"{units_text(problem['held'])} you would hold then.", index)
    return added, skipped


def latest_price_currencies() -> dict[str, str]:
    """Each instrument's most recent non-blank price currency over its whole history (one query)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.instrument_id, p.currency FROM prices p JOIN ("
            " SELECT instrument_id, MAX(date) AS d FROM prices WHERE currency IS NOT NULL AND TRIM(currency) != ''"
            " GROUP BY instrument_id) last ON p.instrument_id = last.instrument_id AND p.date = last.d"
        ).fetchall()
    return {r["instrument_id"]: str(r["currency"]).strip() for r in rows}


def get_platforms() -> list[str]:
    """Platforms used in purchases, most recently used first (suggestions for the next purchase)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT (SELECT l2.platform FROM lots l2 WHERE LOWER(l2.platform) = LOWER(l.platform) ORDER BY l2.lot_id DESC LIMIT 1) AS platform "
            "FROM lots l WHERE l.platform IS NOT NULL AND TRIM(l.platform) != '' "
            "GROUP BY LOWER(l.platform) ORDER BY MAX(l.lot_id) DESC LIMIT 20"
        ).fetchall()
    return [r["platform"] for r in rows]


def count_lots(instrument_id: str) -> int:
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM lots WHERE instrument_id = ?", (instrument_id,)).fetchone()[0]


def latest_price_date(instrument_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT max(date) FROM prices WHERE instrument_id = ?", (instrument_id,)).fetchone()
    return row[0] if row else None
