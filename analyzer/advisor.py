"""The advisor layer: providers, persona settings, and the context pack sent with each question.

Privacy: the context is built per provider data level.
- "relative" (default): names, tickers, types, currencies, weights, % changes, indicators.
- "full": additionally amounts in the reporting currency, quantities, average costs and purchase lots with dates.
"""
from __future__ import annotations

import json
import uuid

import pandas as pd

from analyzer import db, research, secrets_store
from analyzer.engine import analyze_history
from analyzer.llm import Provider, complete, validate
from analyzer.persona import DEFAULT_PERSONA, compose_system_prompt, normalize_preferences, normalize_rules

BUILTIN_ID = "claude-login"
MAX_HISTORY_TURNS = 12
MAX_QUESTION_CHARS = 8000


def init_tables() -> None:
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL DEFAULT '',
                data_level TEXT NOT NULL DEFAULT 'relative' CHECK(data_level IN ('relative','full')),
                web_research INTEGER NOT NULL DEFAULT 0,
                effort TEXT NOT NULL DEFAULT 'high',
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ai_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # The subscription login is the first-class route and always exists.
        conn.execute(
            "INSERT OR IGNORE INTO ai_providers(id, name, kind, model, web_research, is_default) "
            "VALUES (?, 'Claude (subscription login)', 'claude_code', 'opus', 1, 1)",
            (BUILTIN_ID,),
        )
        if conn.execute("SELECT count(*) FROM ai_providers WHERE is_default = 1").fetchone()[0] == 0:
            conn.execute("UPDATE ai_providers SET is_default = 1 WHERE id = ?", (BUILTIN_ID,))


# ---------------------------------------------------------------- providers
def _row_to_provider(row) -> Provider:
    return Provider(id=row["id"], name=row["name"], kind=row["kind"], model=row["model"], base_url=row["base_url"],
                    data_level=row["data_level"], web_research=bool(row["web_research"]), effort=row["effort"])


def list_providers() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM ai_providers ORDER BY is_default DESC, created_at").fetchall()
    out = []
    for r in rows:
        p = _row_to_provider(r)
        out.append({
            "id": p.id, "name": p.name, "kind": p.kind, "model": p.model, "base_url": p.base_url,
            "data_level": p.data_level, "web_research": p.web_research, "effort": p.effort,
            "is_default": bool(r["is_default"]), "builtin": p.id == BUILTIN_ID, "local": p.is_local,
            "key_hint": secrets_store.hint(p.secret_name), "can_browse": p.can_browse,
        })
    return out


def get_provider(provider_id: str | None) -> Provider:
    with db.connect() as conn:
        if provider_id:
            row = conn.execute("SELECT * FROM ai_providers WHERE id = ?", (provider_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM ai_providers ORDER BY is_default DESC LIMIT 1").fetchone()
    if row is None:
        raise KeyError("No such AI provider.")
    return _row_to_provider(row)


def save_provider(data: dict, api_key: str | None) -> str:
    pid = data.get("id") or uuid.uuid4().hex[:12]
    if pid == BUILTIN_ID:
        data = {**data, "kind": "claude_code", "base_url": ""}  # the built-in route can change model/settings only
    p = Provider(id=pid, name=str(data.get("name") or "").strip()[:80] or "Model", kind=data["kind"],
                 model=str(data.get("model") or "").strip(), base_url=str(data.get("base_url") or "").strip(),
                 data_level=data.get("data_level", "relative"), web_research=bool(data.get("web_research")),
                 effort=data.get("effort", "high"))
    validate(p)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO ai_providers(id, name, kind, model, base_url, data_level, web_research, effort) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
            "model=excluded.model, base_url=excluded.base_url, data_level=excluded.data_level, "
            "web_research=excluded.web_research, effort=excluded.effort",
            (p.id, p.name, p.kind, p.model, p.base_url, p.data_level, int(p.web_research), p.effort),
        )
    if api_key:
        secrets_store.put(p.secret_name, api_key.strip())
    return pid


def delete_provider(provider_id: str) -> None:
    if provider_id == BUILTIN_ID:
        raise ValueError("The Claude login route cannot be removed (disconnect it in Settings instead).")
    with db.connect() as conn:
        was_default = conn.execute("SELECT is_default FROM ai_providers WHERE id=?", (provider_id,)).fetchone()
        conn.execute("DELETE FROM ai_providers WHERE id = ?", (provider_id,))
        if was_default and was_default[0]:
            conn.execute("UPDATE ai_providers SET is_default = 1 WHERE id = ?", (BUILTIN_ID,))
    secrets_store.delete(f"provider:{provider_id}")


def set_data_level(provider_id: str, level: str) -> None:
    """What a model may see: 'relative' (weights, % changes) or 'full' (amounts, quantities, dates)."""
    if level not in ("relative", "full"):
        raise ValueError("data_level must be relative or full")
    with db.connect() as conn:
        if conn.execute("UPDATE ai_providers SET data_level = ? WHERE id = ?", (level, provider_id)).rowcount == 0:
            raise KeyError(f"No such model: {provider_id}")


def set_default(provider_id: str) -> None:
    get_provider(provider_id)
    with db.connect() as conn:
        conn.execute("UPDATE ai_providers SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (provider_id,))


# ---------------------------------------------------------------- persona settings
def get_settings() -> dict:
    with db.connect() as conn:
        rows = dict(conn.execute("SELECT key, value FROM ai_settings").fetchall())
    persona = rows.get("persona") or DEFAULT_PERSONA
    prefs = normalize_preferences(json.loads(rows.get("preferences") or "{}"))
    rules = normalize_rules(json.loads(rows.get("rules") or "[]"))
    return {"persona": persona, "is_default_persona": persona.strip() == DEFAULT_PERSONA.strip(),
            "default_persona": DEFAULT_PERSONA, "preferences": prefs, "rules": rules}


class RulesConflict(ValueError):
    """The rules changed (another tab or device) since the client loaded them."""


def save_settings(persona: str | None, preferences: dict | None, rules: list[str] | None = None,
                  rules_expected: list[str] | None = None) -> dict:
    """Each part is saved only when given, so saving the rules never resets the preferences or vice versa.

    With `rules_expected` (the list the client last saw) the rules are only replaced if the stored list
    still equals it, so a rule added on the phone is not erased by a delete on the desktop.
    """
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # read-compare-write as one step: two devices cannot both win
        if rules is not None and rules_expected is not None:
            row = conn.execute("SELECT value FROM ai_settings WHERE key='rules'").fetchone()
            if normalize_rules(json.loads(row[0]) if row else []) != normalize_rules(rules_expected):
                raise RulesConflict("Your rules were changed on another device or tab. They have been reloaded; try again.")
        if persona is not None:
            text = persona.encode("utf-8", "ignore").decode("utf-8").strip()  # drop lone surrogates
            if not text or text == DEFAULT_PERSONA.strip():
                conn.execute("DELETE FROM ai_settings WHERE key='persona'")
            else:
                conn.execute("INSERT INTO ai_settings VALUES ('persona', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (text[:20000],))
        if preferences is not None:
            # merged over the stored ones: a save that lacks a key (an older tab, a partial update) keeps it
            row = conn.execute("SELECT value FROM ai_settings WHERE key='preferences'").fetchone()
            merged = {**normalize_preferences(json.loads(row[0]) if row else {}), **preferences}
            conn.execute("INSERT INTO ai_settings VALUES ('preferences', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps(normalize_preferences(merged)),))
        if rules is not None:
            kept = normalize_rules(rules)
            if kept:
                conn.execute("INSERT INTO ai_settings VALUES ('rules', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (json.dumps(kept),))
            else:
                conn.execute("DELETE FROM ai_settings WHERE key='rules'")
    return get_settings()


# ---------------------------------------------------------------- context
RELATIVE_ROW_KEYS = ("id", "name", "type", "currency", "symbol", "weight", "pnl_pct", "today_pct", "price_source", "as_of")
FULL_ROW_KEYS = RELATIVE_ROW_KEYS + ("quantity", "price", "avg_cost", "value_base", "cost_base", "pnl_base", "today_base")


def _text_or_none(value) -> str | None:
    """Free text from a DataFrame row: NaN (an old row without the column) and blanks become None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _r(x, nd=4):
    return round(x, nd) if isinstance(x, float) else x


def build_context(snapshot: dict, level: str, instrument_id: str | None = None) -> dict:
    rows = snapshot.get("rows", [])
    keys = FULL_ROW_KEYS if level == "full" else RELATIVE_ROW_KEYS
    holdings, series = [], {}
    for r in rows:
        item = {k: _r(r.get(k)) for k in keys}
        hist = db.get_prices(r["id"])
        if not hist.empty:
            m = analyze_history({"asset_type": r["type"]}, hist)
            item["indicators"] = {k: _r(m.get(k)) for k in ("signal", "score", "return_1m", "return_3m", "return_1y",
                                                         "drawdown", "annual_volatility", "rsi_14")}
            s = hist.set_index("date")["close"].astype(float)
            series[f"{r['id']} ({r['name'][:24]})"] = s[~s.index.duplicated(keep="last")]
        if level == "full":
            lots = db.get_lots(r["id"])
            item["purchases"] = [{"date": l["buy_date"], "quantity": l["quantity"], "unit_price": l["unit_price"],
                                  "paid_amount": l["paid_amount"], "paid_currency": l["paid_currency"],
                                  "platform": _text_or_none(l.get("platform"))}
                                 for l in lots.to_dict("records")]
        holdings.append(item)
    totals = snapshot.get("totals", {})
    exposure: dict[str, float] = {}
    for r in rows:
        if r.get("weight"):
            exposure[r["currency"]] = exposure.get(r["currency"], 0) + r["weight"]
    context = {
        "as_of": snapshot.get("updated_at"),
        "portfolio": {
            "holdings": holdings,
            "totals": ({k: _r(totals.get(k)) for k in ("value_base", "pnl_base", "pnl_pct", "today_base", "today_pct")}
                       if level == "full" else {k: _r(totals.get(k)) for k in ("pnl_pct", "today_pct")}),
            "currency_exposure": {k: round(v, 4) for k, v in exposure.items()},
            "reporting_currency": snapshot.get("base_currency"),
            "fx_to_reporting_currency": snapshot.get("fx"),
        },
        "market": research.market_context(),
        "correlations": research.correlations(series),
        "news": research.recent_news(),
    }
    if instrument_id:
        context["focus_holding"] = instrument_id
    return context


def render_context(ctx: dict) -> str:
    """Context as tagged blocks; the persona tells the model these blocks are data, not instructions."""
    news = ctx.pop("news", [])
    # "<" escaped (still valid JSON), so a name or platform cannot close the data block early
    body = json.dumps({k: v for k, v in ctx.items()}, ensure_ascii=False, default=str).replace("<", "\\u003c")
    news_lines = "\n".join(
        f"- [{n.get('published') or 'undated'}] {n['source']}: {n['title']} — {n.get('summary') or ''} ({n['url']})"
        for n in news
    )
    return (f"<portfolio_and_market>\n{body}\n</portfolio_and_market>\n"
            f"<news>\n{news_lines or '(no recent items)'}\n</news>")


def ask(provider_id: str | None, question: str, history: list[dict], snapshot: dict,
        instrument_id: str | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("Ask a question first.")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"Keep the question under {MAX_QUESTION_CHARS} characters.")
    provider = get_provider(provider_id)
    settings = get_settings()
    ctx = build_context(snapshot, provider.data_level, instrument_id)
    shared = {"data_level": provider.data_level, "holdings": len(ctx["portfolio"]["holdings"]),
              "benchmarks": len(ctx["market"]), "news_items": len(ctx["news"]), "web_research": provider.can_browse}
    system = compose_system_prompt(settings["persona"], settings["preferences"], provider.data_level, provider.can_browse,
                                   rules=settings["rules"])
    turns = [m for m in (history or []) if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    turns = turns[-2 * MAX_HISTORY_TURNS:]
    focus = f"\nFocus on holding {instrument_id}." if instrument_id else ""
    user_msg = f"{render_context(ctx)}\n\n<question>{focus}\n{question}\n</question>"
    result = complete(provider, system, [*turns, {"role": "user", "content": user_msg}])
    return {**result, "provider": {"id": provider.id, "name": provider.name, "kind": provider.kind}, "shared": shared}
