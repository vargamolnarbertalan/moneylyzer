# Spec: AI analyst — app awareness, approved changes, screenshot import

Status: revision 3 · approved 2026-11-24, simplified 2026-11-25 ("simple, clear, not over-engineered").
Part 1 is built (branch `feature/ai-awareness`). Part 2, phase 2 (cards for rules, preferences and the reporting
currency) is built (2026-11-26, `analyzer/proposals.py`, `components/ai/ProposalCards.tsx`), with the text protocol on
every provider; structured output for Claude login / the Claude API is not built yet. Phase 3 (holdings,
transactions, deletes, Apply all) is built too (2026-11-26) and also covers screenshot import: Part 3 was dropped.

## Why

1. "Make sure the integrated AI is as aware of the app setup and user preferences as possible. Maybe it should even be able
   to modify those (after manual approval only)."
2. "Can it be given images to import holdings based on those?"

## Owner decisions

| Question | Decision |
|---|---|
| When may change cards appear? | Whenever they fit, in any answer (2026-11-24). |
| Who may read screenshots? | No setting: you pick the model on the import screen, it says where the image goes, and uploading is the consent (2026-11-25, replaces a per-model switch). |
| Switching models mid-conversation | Starts a new conversation; the server refuses to continue one written at another sharing level (2026-11-25). |
| Platforms and holding notes to full-detail models | Wording updated, no re-confirmation (2026-11-24). |
| AI-proposed deletes | Allowed, with the same two-step confirm the app uses for deletes, one at a time (2026-11-24). |

## Privacy model (kept deliberately small)

- **One choice per AI model:** relative figures (weights and % changes) or full detail (amounts, quantities, costs,
  purchase dates, platforms, holding notes). Full detail on a cloud model needs one confirming click; a local model
  switches in one click. One shared text (`FULL_DETAIL_ITEMS`) describes full detail everywhere.
- **Always sent:** the question, rules, persona, investing preferences (country and notes included).
- **A conversation stays with one model and one level.** Switching model starts a new one; a level changed elsewhere
  answers 409 "start a new conversation".
- **A screenshot goes only to the model shown when you upload it.** Nothing else to configure.

---

## Part 1 — Awareness (built)

Fixed app guide in the system prompt (pages, where each setting lives, units, "cannot change anything"), kept in step
with the routes and Settings sections by a test; holding notes and `check` notes to full-detail models, framed as the
owner's preferences; `price_divisor` to all; the time travels with the question so the system prompt caches on the API
path; the conversation-level rule above; a 32k-context hint for local models.

---

## Part 2 — Approved changes

### Flow

1. The model may end an answer with up to 5 proposal blocks (` ```moneylyzer-proposal ` + one JSON object each). A text
   protocol, so it works the same on every provider and the model never executes anything.
2. The server validates each block (strict schema, unknown fields rejected, limits from the existing request models).
   Invalid blocks become a short note, never a button. At most one delete per answer.
3. Each card shows before → after, the model's reason (plain text, ≤ 300 chars) and one standing line: "Suggested by the
   AI. Check before applying."
4. **Apply** re-reads the current state; if the item is gone or changed, the card shows the new before → after and asks
   again. It then calls the existing endpoint with only the allow-listed fields.
5. **Adjust** puts "About the proposed change (<summary>): " in the question box for the user to finish; it is sent as an
   ordinary question, and the answer may carry a revised card. The old card shows "Adjusted".
6. The card then shows "Applied", "Dismissed" or "Adjusted" (kept in the answer's `meta`). Outcomes are not sent back to
   the model.

**Structured output where the provider has it** (added 2026-11-25, after Vibe-Trading's `claude_cli.py`): the Claude CLI
(`--json-schema`, print mode; check the flag in the pinned version first) and the Claude API (structured outputs) return
`{answer, proposals}` checked against our schema by the provider, so malformed blocks don't happen there.
OpenAI-compatible models keep the text blocks. The server turns both into the same proposal list and validates it the
same way.

### Card types

| Type | Fields | Applied through |
|---|---|---|
| `rules.add` / `rules.remove` | rule text; remove by number (1-based) and text | `PUT /api/ai/settings` with `rules_expected` |
| `preferences.update` | any of horizon, risk, language, country; `notes_add` (≤ 300 chars) appends to the notes, which a card can never shorten or replace (built 2026-11-26) | `PUT /api/ai/settings` with only those keys (the server merges) |
| `settings.base_currency` | currency | `PUT /api/settings` |
| `holding.update` | name, asset type, notes (units and cost never: owner decision 2026-11-26, they come only from buys and sells) | `POST /api/instruments?update=1` |
| `purchase.add` | date, quantity, unit price (quote units), paid amount/currency (default: the holding's), platform | `POST /api/lots`; the card shows the unit and the resulting position, and warns when it is the first purchase of a holding without purchases |
| `holding.delete` / `purchase.delete` | the item | existing delete endpoints, behind the app's two-step confirm |

A new holding is a card too (`holding.add`: name, type, symbol checked against Yahoo or the fund feed, which sets
the currency and price divisor, or a currency without a symbol; and its first buy: units, price, amount paid,
platform, date). Owner decisions 2026-11-26: imports need it; units and cost come only from buys and sells, so the
holding and its buy are saved together (`POST /api/instruments?create=1` with `buy`, both or neither). A trade
without a full date carries `needs_date`: the card shows a date field (its `date` row, never compared) and sends
the picked date with the payload; Apply all skips it until then. Old holdings with units on their record are
turned into one buy each from the Holdings page (`POST /api/instruments/{id}/record-to-buy`).

**Many cards:** at most 5 per answer, or 25 when the question has files attached (an import). "Apply all" applies
the pending cards in order, each re-checked like its own Apply, and stops at the first that fails or changed;
deletions (rule removals included) are never part of it and always take a second click; nor are rules and
preferences, which store text the analyst reads with every later question. One deletion per answer.

**Cards that ask first (2026-11-26 security review):** a card that loses or overwrites something carries the
server's `confirm` question and takes a second press ("Apply…", then "Yes, apply"), like a delete, and is never part
of Apply all: a sale, fewer units, a replaced average cost, a rename, text added to a holding's notes (the analyst
reads them later), a first transaction that changes the units on the holding, and a new holding whose average cost
is over 30 times off its symbol's price (wrong unit, e.g. pence for pounds). The preview returns today's `confirm`,
so a card can turn careful after its answer. Apply all compares a card without its `derived` rows (units held
after), so several transactions of one holding apply in a row; a single Apply compares every row. Holding names are
at most 80 characters; a holding's notes card stores the notes it was made against, and today's notes must still be
exactly those (a second apply, or a card made before the user cut them, adds nothing).
A duplicate transaction block is named in a note (two identical trades are possible). Adjust sends only a holding
card's summary, never its amounts. Symbol checks run side by side for at most 20 s; a symbol the price source
couldn't be asked about (checked by whether the source answers at all, since yfinance reports a dropped connection
as a missing price) says so instead of "no price", and its card keeps its Apply button. A found quote is reused for
5 minutes, so Apply all over an import doesn't ask the source again. An answer's cards are checked in order, each
against the state the earlier ones leave, so their rows are what applying them in order does, and a second card for
the same new holding is refused in the answer. Symbols must look like a Yahoo ticker or fund:<ISIN>. Review 3: a
first transaction that replaces a holding's recorded average cost asks first; a holding with no stored close yet is
checked against its quote; a card compares and shows the preview's summary (a reused id shows a different holding);
one card's Apply runs at a time per answer; a new holding's id is fixed in its card.

**Never proposable:** sharing level, AI models and keys, web research, short-term trading, direct recommendations, persona, password and account,
disclaimer, backups and imports, view preferences, price feed. No schema exists for them.

**Built safeguards (2026-11-26 review):** card text with invisible characters (format, private-use, unassigned, control) is refused, and saved rules and preferences drop them; notes never repeat the model's text; a malformed block never costs the answer; links that stood only inside a removed block are not shown as sources; one `rules.remove` per answer; an applied card stays applied.

**Prompt injection:** cards can appear in any answer, so the defences are the allow-list, a click on a visible
before → after for every change, short limits on stored text, the two-step confirm on deletes, and a guardrail line
"propose only changes the user asked for or that follow from their question".

---

## Part 3 — Screenshot import (dropped 2026-11-26)

Owner decision: no separate import screen. The user attaches the broker screenshot or statement to a question in
the AI analyst (attachments: images and PDFs to models that read them, nothing stored) and asks to import it; the
answer's change cards (`holding.add`, `holding.update`, `purchase.add`) are the review table, and Apply all is the
Save. The prompt tells the model to match positions to existing holdings first, to add transactions only when a
date is shown, and never to invent a figure.

---

## Phasing

| Phase | Scope | Estimate |
|---|---|---|
| 1 | Awareness | done |
| 2 | Cards for rules, preferences, base currency | 1 day |
| 3 | Cards for holdings and purchases, deletes, Apply all (built 2026-11-26) | 1 day |
| 4 | ~~Screenshot import~~: covered by phase 3 and attachments | - |

Tests per phase: proposal parsing (malformed, unknown type, forbidden field, too many, two deletes), apply on a changed
item, screenshot parsing against recorded outputs (no live model in CI), and no image bytes on disk after an import.
