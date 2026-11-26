# Spec: AI analyst — app awareness, approved changes, screenshot import

Status: revision 3 · approved 2026-11-24, simplified 2026-11-25 ("simple, clear, not over-engineered").
Part 1 is built (branch `feature/ai-awareness`). Part 2, phase 2 (cards for rules, preferences and the reporting
currency) is built (2026-11-26, `analyzer/proposals.py`, `components/ai/ProposalCards.tsx`), with the text protocol on
every provider; structured output for Claude login / the Claude API is not built yet. Phase 3 and Part 3 are next.

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
| `holding.update` | name, asset type, notes; quantity / avg cost only for a holding without purchases | `POST /api/instruments?update=1` |
| `purchase.add` | date, quantity, unit price (quote units), paid amount/currency, platform, notes | `POST /api/lots`; the card shows the unit and the resulting position, and warns when it is the first purchase of a holding without purchases |
| `holding.delete` / `purchase.delete` | the item | existing delete endpoints, behind the app's two-step confirm |

A new holding is not a card: the answer points to Holdings → Add holding.

**Never proposable:** sharing level, AI models and keys, web research, short-term trading, direct recommendations, persona, password and account,
disclaimer, backups and imports, view preferences, price feed. No schema exists for them.

**Built safeguards (2026-11-26 review):** card text with invisible characters (format, private-use, unassigned, control) is refused, and saved rules and preferences drop them; notes never repeat the model's text; a malformed block never costs the answer; links that stood only inside a removed block are not shown as sources; one `rules.remove` per answer; an applied card stays applied.

**Prompt injection:** cards can appear in any answer, so the defences are the allow-list, a click on a visible
before → after for every change, short limits on stored text, the two-step confirm on deletes, and a guardrail line
"propose only changes the user asked for or that follow from their question".

---

## Part 3 — Screenshot import

1. Import → **From a screenshot**: pick or paste one PNG, JPEG or WebP; choose the model. The page says: "This image goes
   to <model>. Crop out account numbers and names first."
2. The browser re-encodes the image (drops EXIF, downsizes, refuses very tall captures) and keeps it to show beside the
   review table.
3. `POST /api/import/screenshot` with the raw image (≤ 5 MiB, read into memory, never written to disk). The model is
   called with no tools, an extraction prompt and a JSON schema: one row per position, `null` for anything not visible.
4. **Review table:** each row matched to an existing holding by name, symbol and currency. Rows: Update existing
   (unticked by default, before → after), Add new (opens Add holding pre-filled), Skip.
5. **Save** writes the ticked rows in order and stops at the first error, showing which rows were saved.

**Providers:** Claude API (image block + structured output); OpenAI-compatible (sent as an image; if the model can't read
images, its error is shown); Claude login only if a probe shows `claude -p --input-format stream-json` accepts an image
block, otherwise not offered there.

**Data & privacy entry:** "The cropped image goes to the model you chose. Moneylyzer doesn't store it; the provider keeps
it under its own policy."

**Failures:** not a holdings table, unreadable cells (row blocked until filled or skipped), provider error or refusal
(image discarded, nothing saved), too large (refused in the browser).

---

## Phasing

| Phase | Scope | Estimate |
|---|---|---|
| 1 | Awareness | done |
| 2 | Cards for rules, preferences, base currency | 1 day |
| 3 | Cards for holdings and purchases, deletes | 1 day |
| 4 | Screenshot import (API and OpenAI-compatible first, then the Claude login probe) | 2 days |

Tests per phase: proposal parsing (malformed, unknown type, forbidden field, too many, two deletes), apply on a changed
item, screenshot parsing against recorded outputs (no live model in CI), and no image bytes on disk after an import.
