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

from analyzer.market import COMMON_CURRENCIES
from analyzer.persona import HORIZON_TEXT, MAX_COUNTRY_CHARS, MAX_NOTES_CHARS, MAX_RULE_CHARS, MAX_RULES, strip_hidden

log = logging.getLogger(__name__)

FENCE = "moneylyzer-proposal"
MAX_PROPOSALS = 5
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
            "changes": [{"label": f"Standing rule {index + 1}", "before": state["rules"][index], "after": None}]}


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


KINDS = {"rules.add": _rules_add, "rules.remove": _rules_remove, "preferences.update": _preferences,
         "settings.base_currency": _base_currency}


def check(obj, state: dict) -> dict:
    """One block as a card ({type, payload, summary, changes, reason}), or Invalid. `state`: {"rules": [...],
    "preferences": {...}, "base_currency": "HUF"}, as they are now."""
    if not isinstance(obj, dict):
        raise Invalid("it is not a single JSON object")
    name = obj.get("type")
    kind = KINDS.get(name) if isinstance(name, str) else None
    if kind is None:
        raise Invalid("it is not a change the AI analyst can suggest")
    card = kind(obj, state)  # checks the fields, "reason" included
    card["reason"] = _text(obj["reason"], MAX_REASON_CHARS, "the reason")
    return card


def extract(text: str) -> tuple[str, list[str], bool]:
    """(the answer without its proposal blocks, the blocks' bodies, whether a block was cut off)."""
    bodies = [m.group(1) for m in _BLOCK.finditer(text or "")]
    clean = _BLOCK.sub("", text or "")
    cut = bool(_UNCLOSED.search(clean))
    clean = _UNCLOSED.sub("", clean)
    return re.sub(r"\n{3,}", "\n\n", clean).strip(), bodies, cut


def parse(text: str, state: dict) -> tuple[str, list[dict], list[str]]:
    """The answer without its blocks, the cards (each with an id and status "pending"), and one note per
    block that couldn't become a card (empty when every block was fine). Never raises for a block."""
    clean, bodies, cut = extract(text)
    cards: list[dict] = []
    problems: list[str] = []
    seen: set[str] = set()
    for body in bodies[:MAX_PROPOSALS]:
        try:
            card = check(json.loads(body), state)
            if card["type"] == "rules.remove" and any(c["type"] == "rules.remove" for c in cards):
                raise Invalid("only one removal is suggested per answer")
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
        if key not in seen:  # the same change twice is one card
            seen.add(key)
            cards.append({"id": uuid.uuid4().hex[:12], **card, "status": "pending"})
    if len(bodies) > MAX_PROPOSALS:
        problems.append(f"only {MAX_PROPOSALS} suggestions are shown per answer")
    if cut:
        problems.append("the answer was cut off in the middle of one")
    notes = [f"A suggested change couldn't be shown as a button: {p}." for p in problems]
    return clean, cards, notes


def preview(kind: str, payload: dict, state: dict) -> dict:
    """A stored card checked against today's state: {"ok": True, "summary", "changes", "payload"} or
    {"ok": False, "reason"} (already applied elsewhere, the rule is gone, ...). Never trusts the stored
    payload beyond what check() accepts."""
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "This suggestion is not readable any more."}
    try:
        card = check({**payload, "type": kind, "reason": "-"}, state)  # the kind can't come from the payload
    except Invalid as exc:
        return {"ok": False, "reason": f"This can't be applied now: {exc}."}
    except Exception:
        return {"ok": False, "reason": "This suggestion is not readable any more."}
    return {"ok": True, "summary": card["summary"], "changes": card["changes"], "payload": card["payload"]}
