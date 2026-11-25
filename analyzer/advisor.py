"""The advisor layer: providers, persona settings, and the context pack sent with each question.

Privacy: the context is built per provider data level.
- "relative" (default): names, tickers, types, currencies, weights, % changes, indicators, and whether a
  holding is closed (everything sold).
- "full": additionally amounts in the reporting currency, quantities, average costs, realized P/L, transactions
  (buys and sells, under "purchases") with dates and platforms, holding notes and data checks.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone

import pandas as pd

from analyzer import db, research, secrets_store
from analyzer.engine import analyze_history
from analyzer.llm import Provider, applied_effort, complete, takes_effort, validate
from analyzer.persona import DEFAULT_PERSONA, compose_system_prompt, normalize_preferences, normalize_rules

BUILTIN_ID = "claude-login"
MAX_HISTORY_TURNS = 12
MAX_QUESTION_CHARS = 8000
CONVERSATION_KEEP_DAYS = 90
MAX_CONVERSATIONS = 100
MAX_SAVED_MESSAGES = 200
CONVERSATION_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,40}")
log = logging.getLogger("lia")


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
            -- Saved AI analyst conversations: the questions and answers (with model, effort, sources and the
            -- "what was shared" summary), never the portfolio context sent with them. Deleted after
            -- CONVERSATION_KEEP_DAYS without a new answer; at most MAX_CONVERSATIONS are kept.
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                provider_id TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                messages TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        # The subscription login is the first-class route and always exists.
        conn.execute(
            "INSERT OR IGNORE INTO ai_providers(id, name, kind, model, web_research, effort, is_default) "
            "VALUES (?, 'Claude (subscription login)', 'claude_code', 'opus', 1, 'default', 1)",
            (BUILTIN_ID,),
        )
        # Before effort reached Claude Code, Claude login rows stored "high" that was never applied: answers ran
        # at the model's default. Once, make that explicit, so they keep running as they did.
        if conn.execute("SELECT 1 FROM ai_settings WHERE key = 'claude_login_effort_default'").fetchone() is None:
            conn.execute("UPDATE ai_providers SET effort = 'default' WHERE kind = 'claude_code'")
            conn.execute("INSERT INTO ai_settings(key, value) VALUES ('claude_login_effort_default', '1')")
        if conn.execute("SELECT count(*) FROM ai_providers WHERE is_default = 1").fetchone()[0] == 0:
            conn.execute("UPDATE ai_providers SET is_default = 1 WHERE id = ?", (BUILTIN_ID,))
    purge_conversations()  # at every start: retention holds even if the AI analyst is never opened


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
            "takes_effort": takes_effort(p),
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
                 data_level="relative", web_research=bool(data.get("web_research")),
                 effort=data.get("effort", "high"))
    validate(p)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO ai_providers(id, name, kind, model, base_url, data_level, web_research, effort) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
            "model=excluded.model, base_url=excluded.base_url, "
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
    """What a model may see: 'relative' (weights, % changes, whether a holding is sold out) or 'full' (also
    amounts, quantities, costs, dates, platforms and holding notes)."""
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


class LevelChanged(Exception):
    """The conversation was written at another sharing level than the model now has: its earlier answers
    (perhaps with amounts) must not reach a model that may only see relative figures."""


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
FULL_ROW_KEYS = RELATIVE_ROW_KEYS + ("quantity", "price", "avg_cost", "value_base", "cost_base", "pnl_base", "today_base",
                                     "realized_base")


def _text_or_none(value) -> str | None:
    """Free text from a DataFrame row: NaN (an old row without the column) and blanks become None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _r(x, nd=4):
    """Rounded floats; NaN (a missing value in a DataFrame column) becomes None, so the block stays valid JSON."""
    if isinstance(x, float):
        return None if math.isnan(x) else round(x, nd)
    return x


def build_context(snapshot: dict, level: str, instrument_id: str | None = None) -> dict:
    rows = snapshot.get("rows", [])
    keys = FULL_ROW_KEYS if level == "full" else RELATIVE_ROW_KEYS
    holdings, series = [], {}
    instruments = {str(i["instrument_id"]): i for i in db.get_instruments().to_dict("records")}
    for r in rows:
        item = {k: _r(r.get(k)) for k in keys}
        inst = instruments.get(str(r["id"]), {})
        item["price_divisor"] = _r(float(inst.get("price_divisor") or 1))
        item["closed"] = bool(r.get("closed"))  # everything sold: no weight, no value; says nothing about amounts
        if level == "full":
            notes = _text_or_none(inst.get("notes"))
            if notes:
                item["notes"] = " ".join(notes.split())[:1000]  # the owner's own words; CSV imports have no cap
            if r.get("check"):
                item["check"] = r["check"]  # data problems (may quote units), full detail only
        hist = db.get_prices(r["id"])
        if not hist.empty:
            m = analyze_history({"asset_type": r["type"]}, hist)
            item["indicators"] = {k: _r(m.get(k)) for k in ("signal", "score", "return_1m", "return_3m", "return_1y",
                                                         "drawdown", "annual_volatility", "rsi_14")}
            s = hist.set_index("date")["close"].astype(float)
            series[f"{r['id']} ({r['name'][:24]})"] = s[~s.index.duplicated(keep="last")]
        if level == "full":
            lots = db.get_lots(r["id"])
            # buys and sells (the key keeps its name); for a sell paid_amount is what was received
            item["purchases"] = [{"date": l["buy_date"], "side": l["side"], "quantity": _r(l["quantity"]),
                                  "unit_price": _r(l["unit_price"]),
                                  "paid_amount": _r(l["paid_amount"]), "paid_currency": _text_or_none(l["paid_currency"]),
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
            "totals": ({k: _r(totals.get(k)) for k in ("value_base", "pnl_base", "pnl_pct", "today_base", "today_pct", "realized_base")}
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
    ).replace("<", "＜")  # third-party text: a decoded "</news>" must not end the block
    return (f"<portfolio_and_market>\n{body}\n</portfolio_and_market>\n"
            f"<news>\n{news_lines or '(no recent items)'}\n</news>")


def ask(provider_id: str | None, question: str, history: list[dict], snapshot: dict,
        instrument_id: str | None = None, now: datetime | None = None, expected_level: str | None = None,
        effort: str | None = None, conversation_id: str | None = None, turn_id: str | None = None) -> dict:
    """`effort` overrides the model's saved effort for this question only (chosen in the chat; never saved).
    With `conversation_id` the question and its answer are saved to that conversation once answered."""
    question = (question or "").strip()
    if not question:
        raise ValueError("Ask a question first.")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"Keep the question under {MAX_QUESTION_CHARS} characters.")
    provider = get_provider(provider_id)
    if effort is not None and takes_effort(provider):  # other models take none: the choice is ignored
        provider = dataclasses.replace(provider, effort=effort)
        validate(provider)
    # A conversation with answers must say which level they were written at, and match the model's level
    # (a tab with older code sends none). A new conversation is never refused.
    answered = any(m.get("role") == "assistant" for m in (history or []))
    if answered and expected_level != provider.data_level:
        raise LevelChanged("This model's sharing level changed since this conversation started. "
                           "Start a new conversation to go on.")
    settings = get_settings()
    ctx = build_context(snapshot, provider.data_level, instrument_id)
    shared = {"data_level": provider.data_level, "holdings": len(ctx["portfolio"]["holdings"]),
              "notes": sum(1 for h in ctx["portfolio"]["holdings"] if h.get("notes")),
              "benchmarks": len(ctx["market"]), "news_items": len(ctx["news"]), "web_research": provider.can_browse}
    system = compose_system_prompt(settings["persona"], settings["preferences"], provider.data_level, provider.can_browse,
                                   rules=settings["rules"])
    turns = [m for m in (history or []) if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    turns = turns[-2 * MAX_HISTORY_TURNS:]
    focus = f"\nFocus on holding {instrument_id}." if instrument_id else ""
    # The time travels with the question, not in the system prompt, which stays a stable cacheable prefix.
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    user_msg = f"Current date and time (UTC): {stamp}.\n{render_context(ctx)}\n\n<question>{focus}\n{question}\n</question>"
    result = complete(provider, system, [*turns, {"role": "user", "content": user_msg}])
    answer = {**result, "provider": {"id": provider.id, "name": provider.name, "kind": provider.kind},
              "effort": applied_effort(provider), "shared": shared}
    if conversation_id:
        try:
            save_turn(conversation_id, provider.id, question, answer, instrument_id, now=now, turn_id=turn_id)
        except Exception as exc:  # a failed save must not cost the answer
            log.warning("could not save the AI conversation: %s", exc)
    return answer


# ---------------------------------------------------------------- saved conversations
def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")  # sqlite datetime() format, UTC


def _purge(conn, now: datetime | None = None) -> None:
    """Expired (no answer for CONVERSATION_KEEP_DAYS) and over-the-cap conversations go."""
    cutoff = ((now or datetime.now(timezone.utc)) - pd.Timedelta(days=CONVERSATION_KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM ai_conversations WHERE updated_at < ?", (cutoff,))
    conn.execute("DELETE FROM ai_conversations WHERE id NOT IN "
                 "(SELECT id FROM ai_conversations ORDER BY updated_at DESC, created_at DESC LIMIT ?)", (MAX_CONVERSATIONS,))


def _connect_for_deletes(conn) -> None:
    # Freed pages are overwritten with zeros, so a deleted conversation's text is not left in the file
    # (and so not in a later backup download, which copies the file page by page).
    conn.execute("PRAGMA secure_delete = ON")


def purge_conversations(now: datetime | None = None) -> None:
    """Run at startup and before a backup, so retention holds even when nobody uses the AI analyst."""
    with db.connect() as conn:
        _connect_for_deletes(conn)
        _purge(conn, now)


def save_turn(conversation_id: str, provider_id: str, question: str, answer: dict, instrument_id: str | None = None,
              now: datetime | None = None, turn_id: str | None = None) -> None:
    """Append one answered question to a saved conversation (created on its first answer).

    `turn_id` is the browser's id for the question: a retried question (the first answer may have been
    saved after the browser gave up waiting) replaces its earlier answer instead of being added twice.
    """
    if not CONVERSATION_ID_RE.fullmatch(conversation_id or "") or (turn_id and not CONVERSATION_ID_RE.fullmatch(turn_id)):
        raise ValueError("Bad conversation id.")
    stamp = _stamp(now)
    meta = {k: answer.get(k) for k in ("provider", "model", "effort", "sources", "shared")}
    reply = {"id": uuid.uuid4().hex[:12], "role": "assistant", "content": answer.get("text", ""), "meta": meta}
    with db.connect() as conn:
        _connect_for_deletes(conn)
        conn.execute("BEGIN IMMEDIATE")  # read-append-write as one step: two tabs saving at once both keep their turn
        _purge(conn, now)  # an expired conversation is gone before it could be continued
        row = conn.execute("SELECT messages FROM ai_conversations WHERE id = ?", (conversation_id,)).fetchone()
        messages = json.loads(row["messages"]) if row else []
        asked = next((i for i, m in enumerate(messages) if turn_id and m.get("role") == "user" and m.get("turn") == turn_id), None)
        if asked is not None:
            if asked + 1 < len(messages) and messages[asked + 1].get("role") == "assistant":
                messages[asked + 1] = reply
            else:
                messages.insert(asked + 1, reply)
        else:
            user = {"id": uuid.uuid4().hex[:12], "role": "user", "content": question}
            if turn_id:
                user["turn"] = turn_id
            if instrument_id:
                user["holding"] = instrument_id
            messages += [user, reply]
        payload = json.dumps(messages[-MAX_SAVED_MESSAGES:])  # ASCII escapes: a lone surrogate cannot fail the save
        conn.execute(
            "INSERT INTO ai_conversations(id, title, provider_id, model, created_at, updated_at, messages) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET provider_id = excluded.provider_id, "
            "model = excluded.model, updated_at = excluded.updated_at, messages = excluded.messages",
            (conversation_id, " ".join(question.split())[:80], provider_id, answer.get("model"), stamp, stamp, payload))
        _purge(conn, now)  # the cap counts this one too


def list_conversations(now: datetime | None = None) -> list[dict]:
    """Newest first, without their messages. `model` is the one that last answered there."""
    with db.connect() as conn:
        _connect_for_deletes(conn)
        _purge(conn, now)
        rows = conn.execute(
            "SELECT c.id, c.title, c.provider_id, c.model, c.created_at, c.updated_at, "
            "json_array_length(c.messages) AS count, p.name AS provider_name FROM ai_conversations c "
            "LEFT JOIN ai_providers p ON p.id = c.provider_id ORDER BY c.updated_at DESC, c.created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str, now: datetime | None = None) -> dict:
    with db.connect() as conn:
        _connect_for_deletes(conn)
        _purge(conn, now)
        row = conn.execute("SELECT id, title, provider_id, updated_at, messages FROM ai_conversations WHERE id = ?",
                           (conversation_id,)).fetchone()
    if row is None:
        raise KeyError("No such conversation (it may have been deleted).")
    return {**dict(row), "messages": json.loads(row["messages"])}


def delete_conversation(conversation_id: str | None = None) -> int:
    """Delete one conversation, or all of them when no id is given. Returns how many went."""
    with db.connect() as conn:
        _connect_for_deletes(conn)
        if conversation_id is None:
            return conn.execute("DELETE FROM ai_conversations").rowcount
        return conn.execute("DELETE FROM ai_conversations WHERE id = ?", (conversation_id,)).rowcount
