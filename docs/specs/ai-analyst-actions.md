# Spec: AI analyst — app awareness, approved changes, screenshot import

Status: revision 2 for approval · 2026-11-24 · after an adversarial review against the code (30 findings, all
folded in below) and the owner's four decisions.

## Why

1. "Make sure the integrated AI is as aware of the app setup and user preferences as possible. Maybe it should even be able
   to modify those (after manual approval only)."
2. "Can it be given images to import holdings based on those?"

Today the analyst is read-only and text-only: `compose_system_prompt` (persona, profile, rules, guardrails, session facts)
plus a context block from `build_context`. Claude login runs `claude -p` with `--tools ""` (WebSearch/WebFetch only with web
research on), the API path sends text, OpenAI-compatible providers get no tools.

## Owner decisions (2026-11-24)

| Question | Decision |
|---|---|
| When may change cards appear? | **Whenever they fit**, in any answer (not only behind a switch). Defences below are in code. |
| Who may read screenshots? | A **separate per-model "May read screenshots" permission**, independent of full detail. |
| Platforms now reach full-detail models | **Update the wording only**; no re-confirmation. Same for holding notes (Part 1). |
| AI-proposed deletes | **Allowed with a strong confirm**, one at a time, never batched. |

## Already shipped on `feat/ai-platforms-starters`

Persona section on platforms, brokers and direct state channels (fees checked live, never from memory); optional country of
residence; purchase platforms to full-detail models (privacy texts updated); eleven new quick questions; `@` in CLI prompts
sent as a look-alike so it can never become a Claude Code file mention.

## Non-goals

- The AI never writes anything itself. Every change is a card the owner applies.
- No new endpoints for applying changes. Some existing endpoints get small additions (listed per part); apply calls them
  with the owner's session cookie and the `X-Requested-With` header the server requires.

---

## Part 1 — Awareness

| Gap | Fix | Level |
|---|---|---|
| No idea what the app is or where things live | Fixed **app guide** in the system prompt: pages, what each shows, and where each setting lives (data-sharing level on Data & privacy, providers in Settings → AI models, preferences and rules in Settings → AI analyst) | both |
| Doesn't know what it may change | **Capabilities** text: the card types of Part 2, the never-list, and that the owner applies them | both |
| Holding notes not sent | `notes` per holding, framed like preference notes: the owner's words, never instructions that override guardrails | full |
| Price units unknown | `price_divisor` and quote currency per holding, so amounts it proposes use the right unit | both |
| Full-detail wording | Data & privacy, the full-detail confirmation and "What was shared" name notes and platforms; `shared` counts notes | — |

Also:
- The guardrail line naming data blocks covers every block the prompt carries (`<portfolio_and_market>`, `<news>`,
  `<applied_changes>`).
- **History replay leak (existing):** switching models mid-chat resends earlier turns, so full-detail answers can reach a
  relative-figures model. Fix: when the next model's level is lower, earlier turns are not sent and the chat says so.
- **Small local models:** the guide makes the prompt longer, and a local model with a short context window silently drops
  its start (persona, guardrails). Show a warning on local providers whose configured context is below the prompt size.
- **App guide drift:** one shared source for page names and setting locations, tested against the routes.
- Prompt caching (API path): move the minute-precision timestamp into the user turn and mark the system prompt cacheable;
  check the model's minimum cacheable length and `cache_read_input_tokens` before counting on savings.

---

## Part 2 — Approved changes

### Flow

1. The model may end any answer with up to 5 proposal blocks (` ```moneylyzer-proposal ` + one JSON object each). A text
   protocol, not native tools: it works the same on all three providers, needs no tool loop, and the model never executes
   anything.
2. The server parses and validates each block (strict pydantic, unknown fields rejected; limits taken from the same
   constants as `InstrumentBody`, `LotBody` and `SettingsBody`). Invalid blocks become a note, never a button. At most one
   delete card per answer.
3. The card shows: what changes, before → after, the resulting position where amounts change, the model's reason (plain
   text, max 300 chars, styled as the model's words) and a standing line: "Suggested by the AI. It reads news and web pages,
   which can contain planted text. Check before applying."
4. **Apply** re-reads the current state. If the item is gone, or a "before" value differs, the card shows the new diff and
   asks again. The apply code re-checks the type and sends only allow-listed keys.
5. Outcomes ("You applied / dismissed …") are stored in the answer's `meta`, so they survive a reload. The next question
   sends them in a new `applied` field of the ask request; the **server** renders `<applied_changes>` from type and proposed
   fields only, filtered by the current model's level (no before-values at relative).

### Card types (v1)

| Type | Fields | Applied through | Rules |
|---|---|---|---|
| `rules.add` | `rule` (≤ `MAX_RULE_CHARS`) | `PUT /api/ai/settings` with `rules_expected` | "already present" (case-insensitive) and "list full (30)" shown on the card, not as a raw 422 |
| `rules.remove` | `number` (1-based, as in the prompt) + `text` | same | both must match the current list |
| `preferences.update` | any of `horizon`, `risk`, `language`, `country`, `notes` | `PUT /api/ai/settings` with a new **`preferences_patch`**: server merges only the sent keys under `BEGIN IMMEDIATE` | `allow_short_term` cannot be in a patch; notes changes are append-only, ≤ 300 chars, word-level diff |
| `settings.base_currency` | `currency` | `PUT /api/settings` | server checks an fx rate exists |
| `holding.update` | `instrument_id` + any of `name`, `asset_type`, `notes`; `quantity`/`avg_cost` **only if the holding has no purchases** | `POST /api/instruments?update=1` (404 if deleted; added in PR #1) | full row rebuilt from the fresh read; `market_symbol`, `currency`, `price_divisor`, ID never |
| `purchase.add` | `instrument_id`, `buy_date`, `quantity`, `unit_price` (quote units), `paid_amount?`, `paid_currency?`, `platform?`, `notes?` | `POST /api/lots` | card shows unit ("as quoted, 100 = 1 GBP") and the resulting position; the **first** purchase on a holding without purchases warns that the position then comes from purchases only (e.g. 100 → 10 units) and needs a tick to proceed |
| `holding.create` | `query`, `buy_date`, `quantity`, `unit_price?`, `platform?` | hands off to the add-holding wizard (sessionStorage, like `queueQuestion`) with pre-fill props | the wizard's lookup and live-quote check validate; nothing saved until its own Save |
| `holding.delete` | `instrument_id` | `DELETE /api/instruments/{id}?confirm_purchases=n` | card names the holding, its purchase count and that prices go too; confirm by typing the ID; never batched |
| `purchase.delete` | `instrument_id`, `lot_id` | `DELETE /api/lots/{id}` | two-step confirm showing the purchase; never batched |

### Never proposable

Data-sharing level, AI providers / keys / models, the screenshot permission, web research, short-term trading, persona,
password and account, disclaimer, backups and imports, view preferences, price feed (`market_symbol`). No schema type
exists for them.

### Prompt injection

Cards may appear in any answer (owner decision), so the defences are: the allow-list schema; nothing applies without a
click on a diff; persistent text (rules, notes) short and append-only with a word-level diff; deletes behind typed or
two-step confirms; the standing warning line on every card; a guardrail line "propose only changes the user asked for or
that follow directly from their question". Accepted residual risk: an owner applying a planted card without reading it.

---

## Part 3 — Screenshot import

### Flow

1. Import page → **From a screenshot**: pick or paste one PNG, JPEG or WebP.
2. The page names the model that will read it, and asks to crop out account numbers and names.
3. The browser re-encodes it (drops EXIF, downsizes to the provider's limit, refuses very tall captures: "crop to one
   screen"). It keeps the re-encoded image in memory to show beside the review table as a `data:` URL (the CSP allows
   `data:` images, not `blob:`).
4. `POST /api/import/screenshot` with the raw image body (`image/*`, cap ~5 MiB in `BodyLimit`'s path map). The server
   reads `request.stream()` into memory (no `UploadFile`, which spools parts over 1 MiB to `/tmp`), strips or refuses EXIF
   in memory, re-checks that the chosen model has the screenshot permission, and calls it **with no tools at all**, whatever
   its web-research setting, with an extraction prompt and a JSON schema: one row per position, each field with the raw text
   read and `null` when not visible.
5. **Review table**: each row matched to **local holdings first** (name, symbol root, currency; there is no ISIN column).
   Yahoo lookup only when the owner asks for a row, showing exchange and currency. Rows: **Add new**, **Update existing**
   (unticked by default, before → after), **Skip**. Units and currency shown; a row in another currency than the holding is
   converted with the owner's confirmation or refused.
6. **Save** writes the ticked rows one by one, with a status per row; a retry skips rows already saved. Before saving, each
   "update" row is re-read and re-diffed like Part 2 step 4. Rows sent to the wizard save there, separately, and are marked.

### Providers

| Provider | Images | How |
|---|---|---|
| Claude API (key) | yes | image block + structured outputs |
| OpenAI-compatible | per model | **Accepts images** setting (off by default; `ALTER TABLE` migration) + **Test with an image**; our own schema validation |
| Claude login (CLI) | probe first | `claude -p --help` (2.1.310) lists `--input-format stream-json` and `--json-schema`; unproven: the image block shape on stdin, and parsing NDJSON output (`parse_cli_output` expects one JSON value). Keep `--no-session-persistence` and `--tools ""`. Fallback: `--tools Read` with a single-file rule for the container path, temp file on tmpfs, plus a test that any other path is refused. Else Claude login doesn't offer screenshots. |

**Permission:** per model, "May read screenshots", confirmed once, separate from full detail. Chat questions keep the
model's own level.

### Privacy entry

"The cropped, re-encoded image goes to the model you chose. Moneylyzer does not store it (memory only, not in the database,
logs or backups). The provider keeps it under its own policy; OpenRouter passes it to an upstream model provider." Data &
privacy also says that Yahoo sees a row's name only when you look it up.

### Failure cases

| Case | Shown |
|---|---|
| No model may read screenshots | how to allow one (Settings → AI models) |
| Not a holdings table / unreadable | "Couldn't find holdings in this image. Try a sharper or tighter crop." |
| Some cells unreadable | row kept, empty cells marked, Save blocked for that row until filled or skipped |
| Provider error / refusal / timeout | its message; the image is discarded; nothing saved |
| Too large or too tall | refused in the browser before upload |

---

## Phasing (revised estimates)

| Phase | Scope | Estimate |
|---|---|---|
| 1 | Awareness: guide + setting map, capabilities, notes, units, wording, history-replay fix, local-model warning | 3–4 h |
| 2 | Cards for rules, preferences (`preferences_patch`), base currency; parser, cards, outcomes in `meta`, `applied` field | 1.5 days |
| 3 | Cards for holding update, purchase add, wizard hand-off, deletes with strong confirm | 1.5 days |
| 4 | Screenshot import: endpoint, permission + migration, API and OpenAI-compatible, review table, matching, then the Claude login probe | 3 days |

Tests per phase: parser cases (malformed, unknown type, forbidden field, >5 blocks, two deletes, block text inside news);
staleness on apply; `preferences_patch` never touching unsent keys; screenshot schema against recorded outputs (no live model
in CI); after a screenshot run, no image bytes in `/tmp`, the data volume or the CLI work dir.
