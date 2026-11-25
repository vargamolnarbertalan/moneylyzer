# Changelog

## Unreleased
- Sell transactions: purchases became transactions, each a buy or a sell. Sales use up the oldest buys first (FIFO), so quantity, average cost and cost base follow the units still held, and every sale gets a realized P/L (proceeds minus the cost of the units it used up, each at its own day's exchange rate).
- Realized P/L per sale and per holding on the holding page, and next to Unrealized P/L on the Overview once you have sold something. Unknown pieces (a missing price or exchange rate) show a dash and a data check, never 0.
- A holding whose transactions net to zero units is "Closed": a badge in Holdings, no value or weight, left out of the allocation and the holding counts; its realized P/L still counts. The value-over-time curve subtracts sales from their date.
- A sale larger than the units held on its date is refused (with the CSV line number on import), and a purchase that a later sale needs cannot be deleted before that sale.
- Transactions CSV: an optional `side` column (`buy` or `sell`, empty means buy, so older files still work). Existing data is migrated automatically; every earlier row is a buy.
- AI analyst: with full detail it sees each transaction's side and each holding's realized P/L; both detail levels see whether a holding is closed (sold out).
- A holding whose units come only from its holding record (no transactions yet) cannot be sold from: the Sell option appears once a purchase is recorded. A CSV that lists the newest rows first, with a sale above the buy it sells, imports like an oldest-first one.
- Upgrade note: download a backup before upgrading (Settings → Your data). The database is migrated in place, and an older version knows nothing about sells: if you go back to it after recording a sale, it reads that sale as a purchase.

## 1.1.2
- Phones: no page can scroll sideways any more (iPhone Safari ignored the old guard), and the Scenario ranges table fits narrow screens. Pinch-zoom still works.

## 1.1.1
- Phones: tapping a form field no longer zooms the page in (iPhone Safari), which also let the page scroll sideways. Pinch-zoom still works.

## 1.1.0
- AI analyst knows the app: what each page shows and where each setting lives, and it tells you where to make a change.
- AI analyst compares banks, brokers, trading apps and the state's own channels (for example Magyar Államkincstár via WebKincstár) in your country, checking fees and terms live with sources; without web access it quotes no fees.
- New optional setting: the country you live in (Settings → AI analyst persona).
- Eleven quick questions modelled on paid tools: diversification and risk scores, SWOT, stress test, goal setup, platforms and fees, five perspectives, rules and drift, buying habits, daily briefing and what to watch.
- Simpler privacy: one choice per AI model, relative figures or full detail, confirmed with one click (local models need none). Full detail now also includes purchase platforms and holding notes.
- A conversation stays with one AI model: picking another starts a new one, and a conversation is never continued on a model whose sharing level changed.
- Local models: reachable on Linux Docker too (`host.docker.internal`), with a hint on setting a large enough context window.
- Fixes: oversized requests are refused before anything runs; live tables keep their row order while you move through them with the keyboard; a holding deleted on another device cannot come back from an open edit form; CSV files over 5 MB are refused before uploading; the purchases filter is per page; focus is kept after failed deletes and on Data & privacy.

## 1.0.0
- New UI: React + Vite + TypeScript ("Aurora" design), responsive down to phone width, live updates pushed over Server-Sent Events.
- New server: FastAPI on one port serving the API, the live stream and the built UI; one Docker image (docker compose up -d --build); data on the `lia-data` volume.
- Sign-in for LAN/phone access: first-run setup code from the container log, scrypt password hash, server-side sessions, login rate limit, CSRF header check, strict CSP.
- Claude: connect your Claude subscription from Settings (Claude Code `auth login` inside the container); portfolio-level explanation sends names, types, weights and % changes only.
- One live hub fetches quotes/FX every 60 s, daily histories once a day and fund prices hourly for all tabs, with back-off on failures.
- Review fixes: buy-date FX from a complete daily history (no buy dates sent to Yahoo, today's bar never stored); purchases without price/HUF paid are unknown, never free; live quotes without a currency are refused; atomic purchase import with duplicate skip; deleting a holding with purchases needs explicit confirmation; purchase currency/divisor locked once purchases exist.
- Streamlit UI retired.
- Renamed to **Moneylyzer**; image and container are `moneylyzer` (the data volume keeps its `lia-data` name).
- Display currency of your choice, with each past day converted at that day's rate; value history from 1 week to all time.
- Purchases record the platform, app or bank; an add-holding wizard searches Yahoo tickers and Hungarian fund ISINs and previews the live quote.
- AI analyst (replaces the one-off Claude explanation): your Claude subscription, an API key (Claude, OpenAI, Gemini, Mistral, Groq, OpenRouter, DeepSeek, xAI) or a local model; standing rules, an editable persona and investing preferences; safety guardrails always on top.
- Data & privacy page: every outgoing request with what it sends and reveals, and a per-model choice between relative figures (default) and full detail.
- Markets page: benchmarks and news about your holdings.
- Charts: click two points to compare them (amount, percent, dates on the chart; keyboard and screen-reader controls too).
- Motion and loading: charts draw in, live numbers count to their new value, skeletons on slow loads, pages load on demand; Settings → Appearance → Animations turns it all down (as does the system's reduced-motion setting).
- View preferences (theme, ranges, table sorting, sidebar) follow your account across devices.
- "Not financial advice" notice accepted once before first use, shown again in Settings → About.
- Docker build tolerates slow networks (longer pip timeout and retries).

## 0.3.0
- Live Overview: portfolio total and unrealized P/L in HUF, refreshed every 60 s from Yahoo quotes and intraday FX (EUR/HUF, USD/HUF, ...). Holdings without a live quote use their latest stored price.
- Purchases (buy lots): date, quantity, unit price, optional HUF paid; form on Instruments and CSV import. Quantity and average cost come from purchases when present; HUF cost uses the HUF paid or the buy-date Yahoo FX close (cached in the new fx_rates table).
- Hungarian investment funds priced automatically: market_symbol `fund:<ISIN>` downloads daily prices from OTP Alapkezelő, falling back to BAMOSZ (plain HTTP, only the ISIN is sent).
- Migration hardened: write-locked re-check, atomic backup copy, tolerant of pre-existing orphan rows, safe under concurrent starts. Database backup download uses SQLite's backup API.
- Clearer error when a holdings CSV has no header row.
- Database connections are now always closed (Windows file locks).

## 0.2.0
- AI explanation now uses Claude via the local Claude Code CLI (your Claude plan, no API key) instead of Ollama. Online, on click only; sends computed metrics, never quantities or cost basis.
- Fix: Yahoo daily prices for European/Asian tickers were stored one day early (UTC conversion).
- Fix: blank instrument_id/name/currency in a holdings CSV were stored as the text "nan".
- Fix: holdings are no longer valued with prices quoted in another currency or minor unit (e.g. GBp pence); the Overview flags them.
- Fix: Analysis showed the currency as a green delta arrow next to the latest price.
- Removed the direct requests dependency; replaced deprecated Streamlit use_container_width.
- install_windows.bat / run_windows.bat use Windows (CRLF) line endings.
- Yahoo refresh now saves nothing when Yahoo does not report the quote currency.
- Behaviour change: a holding whose prices are quoted in another currency or minor unit (e.g. an LSE ticker in GBp) is no longer valued; the Check column says how to fix it.
- If you stored Yahoo prices with 0.1.0, delete those instruments' price history (or the instruments) and refresh again: 0.1.0 saved European/Asian closes one day early.
- Requires Streamlit 1.67 or newer.
- New asset type `commodity` (e.g. gold), scored by the same price-trend rules. An existing 0.1.0 database is migrated once on start; a copy is kept as data/investment_analyzer.before-migration-<date-time>.sqlite3 (0.2.0 builds named it .before-commodity.sqlite3).

## 0.1.0
- Initial local Streamlit dashboard.
- SQLite storage for instruments and daily prices.
- CSV import/export and optional yfinance history refresh.
- Price-only indicators, signal rules and historical bootstrap scenarios.
- Optional local Ollama metric explanation.
