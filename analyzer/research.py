"""Market context and news the advisor reads: computed in Python, never invented by a model.

- Benchmarks: daily closes of global indices, rates, FX, commodities (Yahoo, verified 2026-11-24).
- Correlations: 90-day daily-return correlations of each holding against the benchmarks and each other.
- News: official RSS/Atom feeds (central banks, statistics offices) plus Yahoo per-ticker headlines.
  Feed text is untrusted: it is stored as plain text, length-capped and passed to models as data.
"""
from __future__ import annotations

import email.utils
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from analyzer import db

BENCHMARKS = [  # symbol, label, kind (kind "yield" closes are percentages, not prices)
    ("^GSPC", "S&P 500", "index"), ("^NDX", "Nasdaq-100", "index"), ("^STOXX50E", "Euro Stoxx 50", "index"),
    ("^GDAXI", "DAX", "index"), ("^N225", "Nikkei 225", "index"), ("IWDA.AS", "MSCI World (IWDA)", "index"),
    ("^VIX", "VIX volatility", "risk"), ("^TNX", "US 10Y yield %", "yield"), ("GC=F", "Gold", "commodity"),
    ("CL=F", "Crude oil", "commodity"), ("EURUSD=X", "EUR/USD", "fx"), ("EURHUF=X", "EUR/HUF", "fx"),
    ("USDHUF=X", "USD/HUF", "fx"), ("BTC-USD", "Bitcoin", "crypto"),
]

# Official feeds that returned valid XML on 2026-11-24 and whose robots.txt allows fetching.
DEFAULT_FEEDS = [
    ("ECB press", "https://www.ecb.europa.eu/rss/press.html"),
    ("ECB statistics", "https://www.ecb.europa.eu/rss/statpress.html"),
    ("Fed monetary policy", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("Fed speeches", "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml"),
    ("MNB news", "https://mnb.hu/enrss/8"),
    ("BIS central bank speeches", "https://www.bis.org/doclist/cbspeeches.rss"),
    ("KSH flash reports (HU)", "https://www.ksh.hu/rss/gyorstajekoztatok"),
]
MAX_FEED_BYTES = 4 * 1024 * 1024
KEEP_PER_FEED = 15
NEWS_DAYS = 21


def init_tables() -> None:
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS benchmark_prices (
                symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS news_items (
                url TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                published TEXT,
                tickers TEXT,
                fetched_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published);
            """
        )


# ---------------------------------------------------------------- benchmarks
def save_benchmark(symbol: str, frame: pd.DataFrame) -> int:
    rows = [(symbol, pd.Timestamp(d).date().isoformat(), float(c))
            for d, c in zip(frame["date"], frame["close"]) if pd.notna(c) and float(c) > 0]
    with db.connect() as conn:
        conn.executemany("INSERT INTO benchmark_prices VALUES (?, ?, ?) ON CONFLICT(symbol, date) DO UPDATE SET close=excluded.close", rows)
    return len(rows)


def _series(symbol: str) -> pd.Series:
    with db.connect() as conn:
        rows = conn.execute("SELECT date, close FROM benchmark_prices WHERE symbol=? ORDER BY date", (symbol,)).fetchall()
    return pd.Series([r["close"] for r in rows], index=pd.to_datetime([r["date"] for r in rows]), dtype=float)


def _change(s: pd.Series, days: int, kind: str) -> float | None:
    if s.empty:
        return None
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    if past.empty:
        return None
    if kind == "yield":  # percentage points, not percent of a percentage
        return float(s.iloc[-1] - past.iloc[-1])
    return float(s.iloc[-1] / past.iloc[-1] - 1)


def market_context() -> list[dict]:
    out = []
    for symbol, label, kind in BENCHMARKS:
        s = _series(symbol)
        if s.empty:
            continue
        rets = np.log(s / s.shift(1)).dropna().tail(30)
        out.append({
            "symbol": symbol, "name": label, "kind": kind, "last": round(float(s.iloc[-1]), 4),
            "as_of": s.index[-1].date().isoformat(),
            "change_1w": _change(s, 7, kind), "change_1m": _change(s, 30, kind), "change_3m": _change(s, 91, kind),
            "change_unit": "pp" if kind == "yield" else "fraction",
            "vol_30d_annualized": float(rets.std(ddof=1) * np.sqrt(252)) if len(rets) > 10 and kind != "yield" else None,
        })
    return out


def correlations(holdings: dict[str, pd.Series], window_days: int = 90) -> dict:
    """Pearson correlation of daily log returns over the last `window_days` calendar days."""
    cols = {}
    for name, s in holdings.items():
        if len(s) > 20:
            cols[name] = s
    for symbol, label, kind in BENCHMARKS:
        if kind in ("index", "commodity", "fx", "crypto"):
            s = _series(symbol)
            if len(s) > 20:
                cols[label] = s
    if len(cols) < 2:
        return {"window_days": window_days, "pairs": [], "note": "Not enough price history yet."}
    frame = pd.DataFrame(cols).sort_index().ffill()
    frame = frame[frame.index >= frame.index.max() - pd.Timedelta(days=window_days)]
    rets = np.log(frame / frame.shift(1)).dropna(how="all")
    corr = rets.corr(min_periods=30)
    pairs = []
    names = list(corr.columns)
    holding_names = set(holdings)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a not in holding_names and b not in holding_names:
                continue  # benchmark-vs-benchmark pairs are noise for this user
            v = corr.loc[a, b]
            if pd.notna(v):
                pairs.append({"a": a, "b": b, "corr": round(float(v), 2)})
    pairs.sort(key=lambda x: -abs(x["corr"]))
    return {"window_days": window_days, "pairs": pairs[:40],
            "note": "Daily log-return correlation; 1 = move together, -1 = opposite, ~0 = unrelated."}


# ---------------------------------------------------------------- news
_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None, limit: int) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    return " ".join(text.split())[:limit]


def _date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(ts):
            return None
        dt = ts.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt.year < 1990:  # MNB uses 1969 placeholders
        return None
    return dt.astimezone(timezone.utc).isoformat()


def parse_feed(payload: bytes) -> list[dict]:
    """RSS 2.0, RSS 1.0 (RDF) and Atom → [{title, url, summary, published}] (untrusted text, capped)."""
    root = ET.fromstring(payload)
    items = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        link = None
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1].lower()
            if ctag == "link":
                link = child.get("href") or (child.text or "").strip() or link
            elif ctag in ("title", "description", "summary", "pubdate", "date", "published", "updated") and ctag not in fields:
                fields[ctag] = child.text or ""
        link = (link or "").strip()
        title = _clean(fields.get("title"), 300)
        if not title or not link.startswith(("http://", "https://")):
            continue
        items.append({
            "title": title, "url": link[:1000],
            "summary": _clean(fields.get("description") or fields.get("summary"), 600),
            "published": _date(fields.get("pubdate") or fields.get("published") or fields.get("date") or fields.get("updated")),
        })
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    return items[:KEEP_PER_FEED]


def fetch_feed(url: str) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "moneylyzer (+https://github.com/vargamolnarbertalan/moneylyzer)"})
    with urllib.request.urlopen(request, timeout=10) as resp:
        payload = resp.read(MAX_FEED_BYTES + 1)
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError("feed larger than 4 MB")
    return parse_feed(payload)


def parse_yahoo_news(items: list, symbol: str) -> list[dict]:
    out = []
    for it in items or []:
        c = (it or {}).get("content") or {}
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or "")
        title = _clean(c.get("title"), 300)
        if not title or not url.startswith(("http://", "https://")):
            continue
        out.append({"title": title, "url": url[:1000], "summary": _clean(c.get("summary") or c.get("description"), 600),
                    "published": _date(c.get("pubDate")), "provider": (c.get("provider") or {}).get("displayName"),
                    "symbol": symbol})
    return out[:10]


def save_news(source: str, items: list[dict], tickers: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        for it in items:
            conn.execute(
                "INSERT INTO news_items(url, source, title, summary, published, tickers, fetched_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET title=excluded.title, summary=excluded.summary, fetched_at=excluded.fetched_at",
                (it["url"], source, it["title"], it.get("summary"), it.get("published"), tickers or it.get("symbol"), now),
            )
        cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=60)).isoformat()
        conn.execute("DELETE FROM news_items WHERE COALESCE(published, fetched_at) < ?", (cutoff,))
    return len(items)


def recent_news(days: int = NEWS_DAYS, limit: int = 60) -> list[dict]:
    cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT source, title, summary, url, published, tickers FROM news_items "
            "WHERE COALESCE(published, fetched_at) >= ? ORDER BY COALESCE(published, fetched_at) DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]
