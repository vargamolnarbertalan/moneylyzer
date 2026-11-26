"""AI analyst change cards (docs/specs/ai-analyst-actions.md, Part 2).

The model may end an answer with up to MAX_PROPOSALS fenced ```moneylyzer-proposal blocks, one JSON object each.
Nothing here changes anything. The blocks are taken out of the answer, checked against a strict allow-list, and
turned into cards whose before -> after comes from the CURRENT settings, never from the model's text. The user
applies a card in the browser through the app's own endpoints; preview() re-checks a card against the state at
that moment first, so a card from an old answer can't apply something that no longer matches.

A text protocol on purpose: it works the same with every provider, and a malformed block only costs a button,
never the answer. Notes never repeat what the model wrote (an injected block must not get an app-styled
billboard), and card text may hold no invisible characters (they would hide an instruction behind the visible
before -> after).
"""
from __future__ import annotations

import json
import logging
import re
import uuid

import pandas as pd

from analyzer.engine import MINOR_UNITS
from analyzer.market import COMMON_CURRENCIES, FUND_PREFIX, fund_isin
from analyzer.persona import HORIZON_TEXT, MAX_COUNTRY_CHARS, MAX_NOTES_CHARS, MAX_RULE_CHARS, MAX_RULES, strip_hidden

log = logging.getLogger(__name__)

FENCE = "moneylyzer-proposal"
MAX_PROPOSALS = 5
MAX_IMPORT_PROPOSALS = 25  # a question with files attached (a broker screenshot to import): owner decision 2026-11-26
MAX_REASON_CHARS = 300
MAX_NOTES_ADD_CHARS = 300  # a card adds to the notes; it never replaces or shortens them
STATUSES = ("pending", "applied", "dismissed", "adjusted")

# The fence may carry an info string ("```moneylyzer-proposal json") and the JSON may start on the same line; the
# body must open with "{" (or "[", read and refused), so a mention of the fence in prose (```moneylyzer-proposal`
# blocks) is left alone.
_BLOCK = re.compile(r"```[ \t]*moneylyzer-proposal\b[^\n{\[`]*\r?\n?[ \t]*([{\[].*?)\s*```", re.S)
# An answer cut off inside a block: an opened body with no closing fence and nothing fenced after it.
_UNCLOSED = re.compile(r"```[ \t]*moneylyzer-proposal\b[^\n{\[`]*\r?\n?[ \t]*[{\[][^`]*\Z", re.S)

PREFERENCE_LABELS = {"horizon": "Investment horizon", "risk": "Risk tolerance", "language": "Answer language",
                     "country": "Country of residence", "notes": "Notes for the AI analyst"}
PREFERENCE_VALUES = {"horizon": tuple(HORIZON_TEXT), "risk": ("conservative", "moderate", "growth"),
                     "language": ("auto", "en", "hu")}
# Preferences only the user may switch (never proposable), by the name the Settings page gives them.
OWNER_ONLY = {"allow_recommendations": "Direct recommendations", "allow_short_term": "Help with short-term trading"}
_SHOWN = {
    "horizon": {k: f"{k} ({v})" for k, v in HORIZON_TEXT.items()},
    "language": {"auto": "the language of the question", "en": "English", "hu": "Hungarian"},
}


class Invalid(ValueError):
    """A block that can't become a card; the message says why, in words the user can read."""


class Retry(Invalid):
    """Can't be checked right now (the price source didn't answer); the same card may work in a moment."""


def _text(value, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise Invalid(f"{what} is not text")
    text = " ".join(value.encode("utf-8", "ignore").decode("utf-8").split())  # one line, no lone surrogates
    if strip_hidden(text) != text:
        raise Invalid(f"{what} contains hidden characters")
    if not text:
        raise Invalid(f"{what} is empty")
    if len(text) > limit:
        raise Invalid(f"{what} is longer than {limit} characters")
    return text


def _fields(obj: dict, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> None:
    if any(k not in (*required, *optional, "type") for k in obj):
        raise Invalid("it has fields Moneylyzer doesn't take")
    if any(k not in obj for k in required):
        raise Invalid("it lacks a field it needs")


def _shown(key: str, value: str) -> str:
    if not value:
        return "not set" if key == "country" else "none"
    return _SHOWN.get(key, {}).get(value, value)


def _rules_add(obj: dict, state: dict) -> dict:
    _fields(obj, ("rule", "reason"))
    rule = _text(obj["rule"], MAX_RULE_CHARS, "the rule")
    rules = state["rules"]
    if rule.casefold() in {r.casefold() for r in rules}:
        raise Invalid("that rule is already in your list")
    if len(rules) >= MAX_RULES:
        raise Invalid(f"you already have the most rules there can be ({MAX_RULES})")
    return {"type": "rules.add", "payload": {"rule": rule}, "summary": "Add a standing rule",
            "changes": [{"label": "New standing rule", "before": None, "after": rule}]}


def _rules_remove(obj: dict, state: dict) -> dict:
    _fields(obj, ("rule", "reason"), ("number",))
    rule = _text(obj["rule"], MAX_RULE_CHARS, "the rule")
    found = [i for i, r in enumerate(state["rules"]) if r.casefold() == rule.casefold()]
    if not found:
        raise Invalid("none of your standing rules has that text")
    index = found[0]
    return {"type": "rules.remove", "payload": {"rule": state["rules"][index]}, "summary": "Remove a standing rule",
            "changes": [{"label": f"Standing rule {index + 1}", "before": state["rules"][index], "after": None}],
            "confirm": "Remove this rule?"}


def _preferences(obj: dict, state: dict) -> dict:
    _fields(obj, ("changes", "reason"))
    asked = obj["changes"]
    if not isinstance(asked, dict) or not asked:
        raise Invalid("it names no preference to change")
    current = state["preferences"]
    notes_now = current.get("notes", "")
    changes, rows = {}, []
    for key, value in asked.items():
        if key in OWNER_ONLY:
            raise Invalid(f"{OWNER_ONLY[key]} can only be switched by you, in Settings → AI analyst persona")
        if key in ("notes_add", "notes"):
            # notes_add: what the model sends. notes: the whole new text, as a stored card's payload holds it; it
            # must still start with today's notes, so a card made before the user edited them can't undo the edit.
            if "notes" in changes:
                raise Invalid("it changes the notes twice")
            if key == "notes_add":
                value = f"{notes_now} {_text(value, MAX_NOTES_ADD_CHARS, 'the text to add to your notes')}".strip()
            else:
                value = _text(value, MAX_NOTES_CHARS, "the notes")
                added = len(value) - len(notes_now) - (1 if notes_now else 0)  # the joining space
                if not value.startswith(notes_now) or added > MAX_NOTES_ADD_CHARS:
                    raise Invalid("it would change or remove part of your notes; it may only add to them")
            if len(value) > MAX_NOTES_CHARS:
                raise Invalid(f"your notes would be longer than {MAX_NOTES_CHARS} characters")
            if value != notes_now:
                changes["notes"] = value
                rows.append({"label": "Add to your notes", "before": None, "after": value[len(notes_now):].strip()})
            continue
        if key not in PREFERENCE_LABELS:
            raise Invalid("it tries to change a setting the AI analyst may not suggest changing")
        label = PREFERENCE_LABELS[key]
        if key in PREFERENCE_VALUES:
            if value not in PREFERENCE_VALUES[key]:
                raise Invalid(f"it gives {label.lower()} a value it can't take")
        else:
            value = _text(value, MAX_COUNTRY_CHARS, label.lower())
        if value == current.get(key, ""):
            continue  # already so: not a change
        changes[key] = value
        rows.append({"label": label, "before": _shown(key, current.get(key, "")), "after": _shown(key, value)})
    if not changes:
        raise Invalid("nothing would change: the preferences are already set that way")
    summary = (("Add to your notes" if rows[0]["label"] == "Add to your notes" else f"Change your {rows[0]['label'].lower()}")
               if len(rows) == 1 else "Change your investing preferences")
    return {"type": "preferences.update", "payload": {"changes": changes}, "summary": summary, "changes": rows}


def _base_currency(obj: dict, state: dict) -> dict:
    _fields(obj, ("currency", "reason"))
    code = obj["currency"].strip().upper() if isinstance(obj["currency"], str) else ""
    if code not in COMMON_CURRENCIES:
        raise Invalid("it is not one of the reporting currencies offered in Settings")
    if code == state["base_currency"]:
        raise Invalid(f"the reporting currency is already {code}")
    return {"type": "settings.base_currency", "payload": {"currency": code}, "summary": "Change the reporting currency",
            "changes": [{"label": "Reporting currency", "before": state["base_currency"], "after": code}]}


# ---- holdings and transactions (phase 3). state["holdings"]: {id: {name, type, currency, symbol, quantity, avg_cost,
# notes, price_divisor, units, lots: [{lot_id, date, side, quantity, unit_price}]}}; state["quote"]: symbol -> quote.
#
# Units and cost come only from buys and sells (owner decision 2026-11-26): a new holding comes with its first buy, and
# no card sets units or cost on a holding's record.
#
# A card that loses or overwrites something (a sale, a first transaction that replaces an old holding's recorded units
# or cost, a new name, text the AI reads later, a price far off the market) or deletes carries "confirm": the question
# the browser asks before a second, explicit press, and such a card is never part of Apply all. Rows marked "derived"
# follow from other cards on the same holding (units held after a transaction): Apply all compares a card without
# them, so two purchases of one holding can be applied in a row. A trade without a date carries "needs_date": the
# card shows a date field (its "date" row) and sends the chosen date with the payload when it is applied.
TYPE_LABELS = {"stock": "Stock", "etf": "ETF", "mutual_fund": "Mutual fund", "government_bond": "Government bond",
               "commodity": "Commodity"}
MAX_NAME_CHARS = 80  # a fund's full name fits; a paragraph of instructions doesn't
MAX_HOLDING_NOTES_CHARS = 1000  # as the holding form takes them (server/main.py)
MAX_PLATFORM_CHARS = 60
MAX_AMOUNT = 1e12  # no unit count, price or amount in a portfolio comes near this
FAR = 30.0  # an average cost this many times off today's price is likely in the wrong unit (pence for pounds)
DELETES = ("rules.remove", "holding.delete", "purchase.delete")
CONFIRM_DELETE = "Delete for good? This can't be undone."
# Price units that are not currencies (GBp: pence); GBp upper-cased would read as pounds.
_MINOR = {code: unit for code, unit in MINOR_UNITS.items() if code != unit[0]}


def _num(x: float) -> str:
    """1234.5 -> "1 234,5", as the app shows numbers (hu-HU)."""
    text = f"{x:,.8f}".rstrip("0").rstrip(".")
    if text.lstrip("-") == "0":
        text = f"{x:.3g}" if x else "0"  # 4e-7 is not 0, and 0 is never "-0"
    return text.replace(",", " ").replace(".", ",")


_TICKER = re.compile(r"\^?[A-Z0-9][A-Z0-9.=-]{0,31}")  # VWCE.DE, BRK-B, EURUSD=X, ^GSPC, 0P0000XXXX.F


def clean_symbol(value) -> str:
    """A proposed symbol as it is checked and stored: an upper-case Yahoo ticker or fund:<ISIN>, or Invalid. Nothing
    else reaches the network (yfinance puts the ticker into a URL path as it is). advisor._prefetch_quotes uses
    this too, so it fetches exactly the symbols the cards will ask about."""
    text = _text(value, 64, "the symbol")
    if text.lower().startswith(FUND_PREFIX):
        isin = fund_isin(text)
        if not isin:
            raise Invalid("the fund symbol is not fund:<ISIN>")
        return FUND_PREFIX + isin
    if not _TICKER.fullmatch(text.upper()):
        raise Invalid("the symbol is not a Yahoo Finance ticker")
    return text.upper()


def _price_unit(currency: str, divisor: float) -> str:
    """What prices are quoted in: "EUR", or "GBp (100 = 1 GBP)" for a holding priced in pence."""
    if divisor == 1:
        return currency
    minor = next((code for code, (major, d) in _MINOR.items() if major == currency and d == divisor), None)
    return f"{minor} ({_num(divisor)} = 1 {currency})" if minor else f"1/{_num(divisor)} {currency}"


def _currency(value, what: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value.strip()):
        raise Invalid(f"{what} is not a currency code")
    code = value.strip()
    minor = _MINOR.get(code) or next((unit for c, unit in _MINOR.items() if c.upper() == code.upper() != unit[0]), None)
    if minor:
        raise Invalid(f"{code} is a price unit ({_num(minor[1])} = 1 {minor[0]}), not a currency; amounts go in {minor[0]}")
    return code.upper()


def _far(value: float, price, unit: str, what: str, rows: list[dict]) -> list[str]:
    """A "Check" row and a reason to ask when `value` is over FAR times off `price` (as quoted); no price, no check.
    The row holds no price, so a re-check reads the same while the price moves."""
    if not value or not price or 1 / FAR <= value / float(price) <= FAR:
        return []
    rows.append({"label": "Check", "before": None,
                 "after": f"The {what} is over {FAR:g} times off the holding's last price; is it per unit, in {unit}?"})
    return [f"gives a {what} over {FAR:g} times off the last price"]


def _last_price(h: dict, state: dict):
    """The holding's last stored close (as quoted), or, before it has one (added by this answer, or moments ago and
    not refreshed yet), its quote; None when neither is known. Checking an answer, the quote comes from the prefetch
    (a holding this answer adds) or is refused without a network call (the state is frozen then)."""
    if h.get("last_price"):
        return h["last_price"]
    if h.get("symbol"):
        try:
            return state["quote"](h["symbol"]).get("price")
        except Exception:
            return None
    return None


def _confirm(careful: list[str]) -> dict:
    return {"confirm": f"This {' and '.join(careful)}. Apply it?"} if careful else {}


def _amount(value, what: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        raise Invalid(f"{what} is not a number")
    if value > MAX_AMOUNT:
        raise Invalid(f"{what} is too large")
    if value < 0 or (positive and value == 0):
        raise Invalid(f"{what} must be {'more than' if positive else 'at least'} 0")
    return float(value)


def _holding(obj: dict, state: dict) -> tuple[str, dict]:
    hid = obj.get("holding")
    if not isinstance(hid, str) or hid not in state["holdings"]:
        raise Invalid("it names a holding you don't have")
    return hid, state["holdings"][hid]


def _date(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):  # \d takes any script's digits
        raise Invalid("the date is not a YYYY-MM-DD date")
    try:
        day = pd.Timestamp(value)
    except ValueError:
        raise Invalid("the date is not a real date") from None
    if day > pd.Timestamp.today().normalize():
        raise Invalid("the date is in the future")
    if day.year < 1900:
        raise Invalid("the date is before 1900")
    return value


TRADE_FIELDS = ("quantity", "unit_price", "paid_amount", "paid_currency", "platform", "date")


def _trade(obj: dict, currency: str, divisor: float, side: str = "buy") -> dict:
    """A buy's or sale's fields from a block: {date (None: the user picks it on the card), quantity, price, paid,
    paid_ccy, platform} and its rows. Paid in the holding's own currency unless the block says otherwise (the app's
    form would assume the reporting currency, which a broker screenshot rarely is); written out, so the card shows
    what will be stored."""
    day = _date(obj["date"]) if obj.get("date") is not None else None
    quantity = _amount(obj.get("quantity"), "the number of units", positive=True)
    price = _amount(obj.get("unit_price", 0), "the price per unit")
    paid = _amount(obj["paid_amount"], "the amount paid", positive=True) if obj.get("paid_amount") is not None else None
    if not price and paid is None:
        raise Invalid("it gives neither a price per unit nor the amount paid")
    paid_ccy = None if paid is None else _currency(
        obj["paid_currency"] if obj.get("paid_currency") is not None else currency, "the currency paid in")
    platform = _text(obj["platform"], MAX_PLATFORM_CHARS, "the platform") if obj.get("platform") else None
    # "date": the row the browser turns into a date field while no date is set; never compared (see ProposalCards)
    rows = [{"label": "Date", "before": None, "after": day or "pick the date below", "date": True},
            {"label": "Units", "before": None, "after": _num(quantity)},
            {"label": "Price per unit", "before": None, "after": f"{_num(price)} {_price_unit(currency, divisor)}"}]
    if paid is not None:
        rows.append({"label": "Paid" if side == "buy" else "Received", "before": None, "after": f"{_num(paid)} {paid_ccy}"})
    if platform:
        rows.append({"label": "Platform", "before": None, "after": platform})
    return {"date": day, "quantity": quantity, "price": price, "paid": paid, "paid_ccy": paid_ccy, "platform": platform,
            "rows": rows}


def _lot(trade: dict, side: str) -> dict:
    return {"side": side, "buy_date": trade["date"], "quantity": trade["quantity"], "unit_price": trade["price"],
            "paid_amount": trade["paid"], "paid_currency": trade["paid_ccy"], "platform": trade["platform"]}


def _trade_payload(trade: dict) -> dict:
    return {"date": trade["date"], "quantity": trade["quantity"], "unit_price": trade["price"], "paid_amount": trade["paid"],
            "paid_currency": trade["paid_ccy"], "platform": trade["platform"]}


def _holding_add(obj: dict, state: dict) -> dict:
    """A new holding, with its first buy when the block gives units: units and cost come only from buys and sells
    (owner decision 2026-11-26), so a holding is never created with units of its own."""
    _fields(obj, ("name", "asset_type", "reason"), ("symbol", "currency", "id", *TRADE_FIELDS))
    name = _text(obj["name"], MAX_NAME_CHARS, "the name")
    kind = obj["asset_type"]
    if kind not in TYPE_LABELS:
        raise Invalid("it gives the holding a type Moneylyzer doesn't have")
    symbol = clean_symbol(obj["symbol"]) if obj.get("symbol") else ""
    buying = obj.get("quantity") not in (None, 0)  # 0: an empty holding, as a model may write it
    if not buying and any(obj.get(k) is not None for k in TRADE_FIELDS if k != "quantity"):
        raise Invalid("it gives a price, payment or date without the number of units bought")
    divisor, price = 1.0, None
    # the same screenshot imported twice, or a fund without a ticker: never a second holding by the same name or symbol
    # ("proposed": added by an earlier card of this answer, see _simulate)
    same = next((h for h in state["holdings"].values() if h["name"].casefold() == name.casefold()), None)
    if same:
        raise Invalid("another suggestion in this answer adds a holding with that name" if same.get("proposed")
                      else "you already have a holding with that name; propose a buy of it instead")
    if obj.get("id") is not None:  # a stored card's: the id it showed (another card may have taken it since)
        if not isinstance(obj["id"], str) or not re.fullmatch(r"[A-Z0-9_-]{1,64}", obj["id"]):
            raise Invalid("its id is not one Moneylyzer makes")
        if obj["id"] in state["holdings"]:
            raise Invalid(f"another holding has taken the id {obj['id']} since")
    if symbol:
        same = next((h for h in state["holdings"].values() if h["symbol"] and h["symbol"].upper() == symbol.upper()), None)
        if same:
            raise Invalid("another suggestion in this answer adds a holding with that symbol" if same.get("proposed")
                          else "you already have a holding with that symbol; propose a buy of it instead")
        try:
            quote = state["quote"](symbol)
        except ValueError as exc:  # symbols.SymbolError
            if getattr(exc, "status", None) == 503:
                raise Retry("its symbol couldn't be checked right now (no connection to the price source)") from None
            raise Invalid("its symbol has no price on Yahoo Finance or the fund price feed") from None
        currency, divisor, price = quote["currency"], float(quote["divisor"] or 1), quote.get("price")
    else:
        if not obj.get("currency"):
            raise Invalid("a holding without a symbol needs its currency")
        currency = _currency(obj["currency"], "the currency")
    trade = _trade(obj, currency, divisor) if buying else None
    base = re.sub(r"[^A-Za-z0-9_-]", "", (symbol or name).split(".")[0].replace(" ", "-")).upper()[:24] or "HOLDING"
    if obj.get("id") is not None:
        hid = obj["id"]  # checked above, before the quote
    else:
        hid, n = base, 2
        while hid in state["holdings"]:
            hid, n = f"{base}-{n}", n + 1
    unit = _price_unit(currency, divisor)
    rows = [{"label": "Name", "before": None, "after": name},
            {"label": "Symbol", "before": None, "after": symbol or "none (prices added by hand)"},
            {"label": "Type", "before": None, "after": TYPE_LABELS[kind]},
            {"label": "Currency", "before": None, "after": currency + (f", prices in {unit}" if divisor != 1 else "")}]
    careful = []
    if trade:
        rows += trade["rows"]
        careful += _far(trade["price"], price, unit, "price per unit", rows)
    else:
        rows.append({"label": "Units", "before": None, "after": "none yet (add its buys)"})
    payload = {"id": hid, "name": name, "asset_type": kind, "symbol": symbol, "currency": None if symbol else currency,
               **(_trade_payload(trade) if trade else {})}
    apply = {"instrument_id": hid, "name": name, "asset_type": kind, "currency": currency, "market_symbol": symbol or None,
             "price_divisor": divisor, "notes": None}
    if trade:
        lot = _lot(trade, "buy")
        del lot["side"]
        apply["buy"] = lot
    card = {"type": "holding.add", "payload": payload, "apply": apply, "changes": rows, **_confirm(careful),
            "summary": f"Add the holding {name}" + (f" with a buy of {_num(trade['quantity'])}" if trade else "")}
    if trade and not trade["date"]:
        card["needs_date"] = True
    return card


def _holding_update(obj: dict, state: dict) -> dict:
    _fields(obj, ("holding", "changes", "reason"))
    hid, h = _holding(obj, state)
    asked = obj["changes"]
    if not isinstance(asked, dict) or not asked:
        raise Invalid("it names nothing to change")
    apply = {"instrument_id": hid, "name": h["name"], "asset_type": h["type"], "currency": h["currency"],
             "market_symbol": h["symbol"] or None, "price_divisor": h["price_divisor"], "notes": h["notes"] or None}
    rows, changes, careful = [], {}, []
    # notes_was: the notes this card was made against (stored by the card, never needed from the model). Today's
    # notes must still be exactly those: a second Apply (another tab) adds nothing, and a card made before the user
    # cut or cleared them can't bring the old text back.
    if "notes_was" in asked and asked["notes_was"] != (h["notes"] or ""):
        raise Invalid("the holding's notes changed since this was suggested")
    for key, value in asked.items():
        if key == "name":
            value = _text(value, MAX_NAME_CHARS, "the name")
            if value != h["name"]:
                if any(i != hid and o["name"].casefold() == value.casefold() for i, o in state["holdings"].items()):
                    raise Invalid("another holding already has that name")
                rows.append({"label": "Name", "before": h["name"], "after": value})
                apply["name"] = changes["name"] = value
                careful.append("renames the holding")
        elif key == "asset_type":
            if value not in TYPE_LABELS:
                raise Invalid("it gives the holding a type Moneylyzer doesn't have")
            if value != h["type"]:
                rows.append({"label": "Type", "before": TYPE_LABELS.get(h["type"], h["type"]), "after": TYPE_LABELS[value]})
                apply["asset_type"] = changes["asset_type"] = value
        elif key == "notes_was":
            if "notes_add" not in asked:
                raise Invalid("it lacks a field it needs")
        elif key == "notes_add":  # a card adds to the holding's notes, never replaces or shortens them
            now = h["notes"] or ""
            added = _text(value, MAX_NOTES_ADD_CHARS, "the text to add to the notes")
            new = f"{now} {added}".strip()
            if len(new) > MAX_HOLDING_NOTES_CHARS:
                raise Invalid(f"the holding's notes would be longer than {MAX_HOLDING_NOTES_CHARS} characters")
            rows.append({"label": "Add to the holding's notes", "before": None, "after": added})
            apply["notes"] = new
            changes["notes_add"], changes["notes_was"] = added, now
            careful.append("adds text to the holding's notes, which the AI analyst reads with your later questions")
        elif key in ("quantity", "avg_cost"):
            raise Invalid("units and cost come only from buys and sells; it can be proposed as a buy or a sale")
        else:
            raise Invalid("it tries to change something about the holding that can't be suggested")
    if not rows:
        raise Invalid("nothing would change: the holding is already like that")
    return {"type": "holding.update", "payload": {"holding": hid, "changes": changes}, "apply": apply,
            "summary": f"Change the holding {h['name']}", "changes": rows, **_confirm(careful)}


def _purchase_add(obj: dict, state: dict) -> dict:
    _fields(obj, ("holding", "side", "quantity", "reason"), ("unit_price", "paid_amount", "paid_currency", "platform", "date"))
    hid, h = _holding(obj, state)
    side = obj["side"]
    if side not in ("buy", "sell"):
        raise Invalid("a transaction is a buy or a sell")
    trade = _trade(obj, h["currency"], h["price_divisor"], side)
    quantity = trade["quantity"]
    if side == "sell" and not h["lots"]:
        raise Invalid("a holding without recorded purchases can't be sold from; add its purchases first")
    if side == "sell" and quantity > h["units"] + 1e-9:
        raise Invalid("it sells more units than the holding has")
    # a holding's first transaction replaces the units on its record: from then on units come from transactions only
    held = h["units"] if h["lots"] else 0.0
    after = held + (quantity if side == "buy" else -quantity)
    # "derived": follows from the other cards of the same holding (see the section comment)
    rows = [{"label": "Transaction", "before": None, "after": "Buy" if side == "buy" else "Sell"}, *trade["rows"],
            {"label": "Units held", "before": _num(h["units"]), "after": _num(after), "derived": True}]
    careful = ["records a sale"] if side == "sell" else []
    careful += _far(trade["price"], _last_price(h, state), _price_unit(h["currency"], h["price_divisor"]), "price per unit", rows)
    if not h["lots"] and h["quantity"]:
        # an old holding with units on its record only (see db.record_to_buy): from now on the transactions give the
        # units and the average cost (engine.analyze), not the record
        cost = h["avg_cost"] is not None
        rows.append({"label": "Note", "before": None, "derived": True,
                     "after": f"Its first transaction: from now on its units{' and average cost' if cost else ''} come "
                              f"from transactions, not the {_num(h['quantity'])} units"
                              f"{' at ' + _num(h['avg_cost']) + ' ' + _price_unit(h['currency'], h['price_divisor']) if cost else ''} on the holding"})
        if abs(after - h["quantity"]) > 1e-9:
            careful.append(f"replaces the {_num(h['quantity'])} units on the holding with {_num(after)}")
        if cost:
            careful.append("replaces the average cost on the holding with its transactions' cost")
    payload = {"holding": hid, "side": side, **_trade_payload(trade)}
    apply = {"instrument_id": hid, **_lot(trade, side)}
    card = {"type": "purchase.add", "payload": payload, "apply": apply, "changes": rows, **_confirm(careful),
            "summary": f"Add a {'purchase' if side == 'buy' else 'sale'} of {h['name']}"}
    if not trade["date"]:
        card["needs_date"] = True
    return card


def _holding_delete(obj: dict, state: dict) -> dict:
    _fields(obj, ("holding", "reason"))
    hid, h = _holding(obj, state)
    lots = len(h["lots"])
    gone = f"{h['name']} ({hid})" + (f", with its {lots} transaction{'s' if lots != 1 else ''}" if lots else "") + " and its stored prices"
    return {"type": "holding.delete", "payload": {"holding": hid}, "apply": {"instrument_id": hid, "confirm_purchases": lots},
            "summary": f"Delete the holding {h['name']}", "changes": [{"label": "Holding", "before": gone, "after": None}],
            "confirm": CONFIRM_DELETE}


def _purchase_delete(obj: dict, state: dict) -> dict:
    _fields(obj, ("holding", "date", "side", "quantity", "reason"))
    hid, h = _holding(obj, state)
    day = _date(obj["date"])
    quantity = _amount(obj["quantity"], "the number of units", positive=True)
    # recorded transactions only (lot_id None: one an earlier card of this answer would add, see _simulate)
    found = [lot for lot in h["lots"] if lot["lot_id"] is not None and lot["date"] == day and lot["side"] == obj["side"]
             and abs(lot["quantity"] - quantity) < 1e-9]
    if len(found) != 1:
        raise Invalid("no single transaction of this holding matches it" if not found else "more than one transaction matches it")
    lot = found[0]
    what = (f"{'Buy' if lot['side'] == 'buy' else 'Sell'} of {_num(lot['quantity'])} on {day} at "
            f"{_num(lot['unit_price'])} {_price_unit(h['currency'], h['price_divisor'])}")
    return {"type": "purchase.delete", "payload": {"holding": hid, "date": day, "side": lot["side"], "quantity": lot["quantity"]},
            "apply": {"lot_id": lot["lot_id"], "instrument_id": hid},
            "summary": f"Delete a transaction of {h['name']}", "changes": [{"label": "Transaction", "before": what, "after": None}],
            "confirm": CONFIRM_DELETE}


KINDS = {"rules.add": _rules_add, "rules.remove": _rules_remove, "preferences.update": _preferences,
         "settings.base_currency": _base_currency, "holding.add": _holding_add, "holding.update": _holding_update,
         "purchase.add": _purchase_add, "holding.delete": _holding_delete, "purchase.delete": _purchase_delete}


def check(obj, state: dict) -> dict:
    """One block as a card ({type, payload, summary, changes, reason}, `apply` for holdings and transactions, and
    `confirm` for one that needs a second press), or Invalid. `state`: {"rules", "preferences", "base_currency",
    "holdings", "quote"}, as they are now (see advisor.proposal_state)."""
    if not isinstance(obj, dict):
        raise Invalid("it is not a single JSON object")
    name = obj.get("type")
    kind = KINDS.get(name) if isinstance(name, str) else None
    if kind is None:
        raise Invalid("it is not a change the AI analyst can suggest")
    # the reason first: a block refused for it asks the price source nothing (kind() checks it is there at all)
    reason = _text(obj["reason"], MAX_REASON_CHARS, "the reason") if "reason" in obj else None
    card = kind(obj, state)
    card["reason"] = reason
    return card


def _simulate(state: dict, card: dict) -> None:
    """Change `state` (parse's own copy: rules, preferences, holdings) as applying `card` would. Holdings it adds are marked
    "proposed"; transactions it adds have no lot_id. Deletions are left out: one per answer, and nothing after it
    may build on it."""
    a, holdings = card.get("apply") or {}, state["holdings"]
    if card["type"] == "holding.add":
        buy = a.get("buy")
        lots = [{"lot_id": None, "date": buy["buy_date"], "side": "buy", "quantity": buy["quantity"],
                 "unit_price": buy["unit_price"]}] if buy else []
        holdings[a["instrument_id"]] = {
            "name": a["name"], "type": a["asset_type"], "currency": a["currency"], "symbol": a["market_symbol"] or "",
            "quantity": 0.0, "avg_cost": None, "notes": "", "price_divisor": a["price_divisor"],
            "units": buy["quantity"] if buy else 0.0, "lots": lots, "proposed": True}
    elif card["type"] == "holding.update":
        h = holdings[a["instrument_id"]]
        holdings[a["instrument_id"]] = {**h, "name": a["name"], "type": a["asset_type"], "notes": a.get("notes") or ""}
    elif card["type"] == "rules.add":
        state["rules"] = [*state["rules"], card["payload"]["rule"]]
    elif card["type"] == "preferences.update":
        state["preferences"] = {**state["preferences"], **card["payload"]["changes"]}
    elif card["type"] == "purchase.add":
        h = holdings[a["instrument_id"]]
        held = h["units"] if h["lots"] else 0.0
        lot = {"lot_id": None, "date": a["buy_date"], "side": a["side"], "quantity": a["quantity"], "unit_price": a["unit_price"]}
        holdings[a["instrument_id"]] = {**h, "lots": [*h["lots"], lot],
                                        "units": held + (a["quantity"] if a["side"] == "buy" else -a["quantity"])}


def extract(text: str) -> tuple[str, list[str], bool]:
    """(the answer without its proposal blocks, the blocks' bodies, whether a block was cut off)."""
    bodies = [m.group(1) for m in _BLOCK.finditer(text or "")]
    clean = _BLOCK.sub("", text or "")
    cut = bool(_UNCLOSED.search(clean))
    clean = _UNCLOSED.sub("", clean)
    return re.sub(r"\n{3,}", "\n\n", clean).strip(), bodies, cut


def _run(bodies: list[str], state: dict) -> tuple[list[dict], list[str]]:
    """The cards and the problems of these blocks, checked in order, each against the state the earlier ones would
    leave (_simulate on a private copy): applied in order, as Apply all does, every card shows what it really does,
    and a second card adding the same holding is refused here instead of stopping Apply all halfway through."""
    cards: list[dict] = []
    problems: list[str] = []
    seen: set[str] = set()
    blocks: set[str] = set()
    state = {**state, "holdings": dict(state.get("holdings") or {})}  # _simulate replaces entries, not the caller's
    for body in bodies:
        try:
            obj = json.loads(body)
            # the same block twice is one card (or one note), said nothing about: checked again after the first, it
            # would be refused as already done (a transaction is the exception: see below)
            same = json.dumps(obj, sort_keys=True) if isinstance(obj, dict) and obj.get("type") != "purchase.add" else None
            if same is not None:
                if same in blocks:
                    continue
                blocks.add(same)
            card = check(obj, state)
            if card["type"] in DELETES and any(c["type"] in DELETES for c in cards):
                raise Invalid("only one removal or deletion is suggested per answer")
        except Retry as exc:  # no card to press later: only asking again brings it back
            problems.append(f"{exc}; ask again in a moment")
            continue
        except Invalid as exc:
            problems.append(str(exc))
            continue
        except ValueError:  # the JSON itself
            problems.append("it was not valid JSON")
            continue
        except Exception as exc:  # whatever a hostile block does (deep nesting, odd types), the answer survives
            log.warning("could not read a change card: %s", type(exc).__name__)
            problems.append("it could not be read")
            continue
        key = json.dumps([card["type"], card["payload"]], sort_keys=True)
        # the same change twice is one card; but two trades without a date look alike until the user dates them
        undated = card["type"] == "purchase.add" and card["payload"].get("date") is None
        if key in seen and not undated:
            if card["type"] == "purchase.add":  # but two identical trades on one day do happen: say one was left out
                problems.append("it repeated another transaction exactly (Moneylyzer keeps one of two identical "
                                "transactions)")
            continue
        seen.add(key)
        cards.append({"id": uuid.uuid4().hex[:12], **card, "status": "pending"})
        _simulate(state, card)
    return cards, problems


def parse(text: str, state: dict, limit: int = MAX_PROPOSALS) -> tuple[str, list[dict], list[str]]:
    """The answer without its blocks, the cards (each with an id and status "pending"), and one note per
    block that couldn't become a card (empty when every block was fine). Never raises for a block. `limit`:
    MAX_PROPOSALS, or MAX_IMPORT_PROPOSALS for a question with files (an import from a screenshot)."""
    clean, bodies, cut = extract(text)
    cards, problems = _run(bodies[:limit], state)
    if len(bodies) > limit:
        problems.append(f"only {limit} suggestions are shown per answer")
    if cut:
        problems.append("the answer was cut off in the middle of one")
    notes = [f"A suggested change couldn't be shown as a button: {p}." for p in problems]
    return clean, cards, notes


def symbols_to_check(text: str, state: dict, limit: int) -> list[str]:
    """The symbols parse() will ask about, in order: the same in-order run with every quote answered by a stand-in,
    so a block refused anyway asks nothing, and one that only passes after an earlier card (a rename that frees a
    name) is asked about. advisor._prefetch_quotes fetches these."""
    asked: list[str] = []

    def stand_in(symbol: str) -> dict:
        if symbol not in asked:
            asked.append(symbol)
        return {"price": None, "currency": "XXX", "quote_currency": "XXX", "divisor": 1.0}

    _run(extract(text)[1][:limit], {**state, "quote": stand_in})
    return asked


def preview(kind: str, payload: dict, state: dict) -> dict:
    """A stored card checked against today's state: {"ok": True, "summary", "changes", "payload", "apply",
    "confirm"} or {"ok": False, "reason"} (already applied elsewhere, the rule is gone, ...), with "retry": True when
    the same card may work in a moment (the price source didn't answer). Never trusts the stored payload beyond
    what check() accepts."""
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "This suggestion is not readable any more."}
    try:
        card = check({**payload, "type": kind, "reason": "-"}, state)  # the kind can't come from the payload
    except Retry as exc:
        return {"ok": False, "reason": f"This can't be applied right now: {exc}; try again in a moment.", "retry": True}
    except Invalid as exc:
        return {"ok": False, "reason": f"This can't be applied now: {exc}."}
    except Exception:
        return {"ok": False, "reason": "This suggestion is not readable any more."}
    if card.get("needs_date"):  # the browser sends the picked date; without one there is nothing to store yet
        return {"ok": False, "reason": "Pick the date on the card first."}
    # `apply`: the body for the app's own endpoint, built here from today's state (holdings and transactions)
    # `confirm`: the question to ask before a second press (today's: a card can turn careful since its answer)
    return {"ok": True, "summary": card["summary"], "changes": card["changes"], "payload": card["payload"],
            "apply": card.get("apply"), "confirm": card.get("confirm")}
