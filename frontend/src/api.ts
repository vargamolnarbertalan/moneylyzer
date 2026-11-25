// Fetch wrapper + the API contract types (FastAPI backend on the same origin).

export type Nullable<T> = T | null;

export interface Row {
  id: string;
  name: string;
  type: string;
  currency: string;
  symbol: Nullable<string>;
  price: Nullable<number>;
  price_currency: Nullable<string>;
  price_source: "live" | "daily" | "stored";
  as_of: Nullable<string>;
  prev_close: Nullable<number>;
  quantity: Nullable<number>;
  avg_cost: Nullable<number>;
  value: Nullable<number>;
  value_base: Nullable<number>;
  cost_base: Nullable<number>;
  pnl: Nullable<number>;
  pnl_base: Nullable<number>;
  pnl_pct: Nullable<number>;
  today_base: Nullable<number>;
  today_pct: Nullable<number>;
  lots: number;
  spark: number[];
  check: string;
  weight: Nullable<number>;
}

export interface Totals {
  value_base: Nullable<number>;
  pnl_base: Nullable<number>;
  pnl_pct: Nullable<number>;
  today_base: Nullable<number>;
  today_pct: Nullable<number>;
  holdings: number;
  valued: number;
  with_pnl: number;
  with_today: number;
}

export interface Snapshot {
  /** The reporting currency: every *_base figure and the totals are in it. */
  base_currency: string;
  rows: Row[];
  totals: Totals;
  /** base-currency units per 1 unit of CCY, for every currency involved (the base itself is omitted). */
  fx: Record<string, Nullable<number>>;
  updated_at: string;
  refresh_seconds: number;
  errors: string[];
}

export interface HistoryPoint {
  date: string;
  value: number;
}

export interface HistoryResponse {
  range: string;
  /** Currency of every point: the base currency the curve was converted to. */
  currency: string;
  points: HistoryPoint[];
  /** Holding ids the stored curve includes (others have no daily prices yet). */
  included?: string[];
}

export interface Instrument {
  instrument_id: string;
  name: string;
  asset_type: string;
  quantity: number;
  avg_cost: Nullable<number>;
  currency: string;
  market_symbol: Nullable<string>;
  price_divisor: number;
  notes: Nullable<string>;
  updated_at?: Nullable<string>;
}

export interface InstrumentInput {
  instrument_id: string;
  name: string;
  asset_type: string;
  quantity: number;
  avg_cost: Nullable<number>;
  currency: string;
  market_symbol: Nullable<string>;
  price_divisor: number;
  notes: Nullable<string>;
}

export interface Metrics {
  observations: number;
  as_of: Nullable<string>;
  latest_price: Nullable<number>;
  return_1m: Nullable<number>;
  return_3m: Nullable<number>;
  return_6m: Nullable<number>;
  return_1y: Nullable<number>;
  drawdown: Nullable<number>;
  annual_volatility: Nullable<number>;
  rsi_14: Nullable<number>;
  sma_50: Nullable<number>;
  sma_200: Nullable<number>;
  score: Nullable<number>;
  signal: string;
  reasons: string[];
  failed_checks?: string[];
  signal_note?: string;
}

export interface Scenario {
  observations_ahead: number;
  p10: number;
  median: number;
  p90: number;
  change_p10: number;
  change_median: number;
  change_p90: number;
}

export interface PricePoint {
  date: string;
  close: number;
}

export interface Lot {
  lot_id: number;
  instrument_id: string;
  buy_date: string;
  quantity: number;
  unit_price: number;
  paid_amount: Nullable<number>;
  paid_currency: Nullable<string>;
  notes: Nullable<string>;
  /** Broker / app / bank the purchase was made at (free text). */
  platform?: Nullable<string>;
}

export interface LotInput {
  instrument_id: string;
  buy_date: string;
  quantity: number;
  unit_price: number;
  paid_amount: Nullable<number>;
  paid_currency: Nullable<string>;
  notes: Nullable<string>;
  platform?: Nullable<string>;
}

/** GET /api/lookup: a candidate for the add-holding wizard. */
export interface LookupResult {
  symbol: string;
  name: string;
  type: "stock" | "etf" | "mutual_fund" | "government_bond" | "commodity";
  exchange: string;
}

/** GET /api/quote: price preview before a holding is saved. */
export interface QuotePreview {
  /** As quoted (pence for a London listing). */
  price: number;
  /** Major currency (GBP for a pence quote). */
  currency: string | null;
  /** As Yahoo/the fund source quotes it ("GBp"). */
  quote_currency: string | null;
  /** Quoted units per major unit (100 for pence); the holding's price_divisor. */
  divisor: number;
  as_of: string | null;
  source: string;
}

export interface InstrumentDetail {
  instrument: Instrument;
  position: Nullable<Row>;
  metrics: Metrics;
  scenarios: Scenario[];
  prices: PricePoint[];
  price_currency: Nullable<string>;
  lots: Lot[];
}

export interface AuthState {
  setup_required: boolean;
  user: Nullable<string>;
}

export interface ClaudeStatus {
  available: boolean;
  loggedIn: boolean;
  authMethod?: Nullable<string>;
  subscription?: Nullable<string>;
  email?: Nullable<string>;
  error?: string;
}

export interface AppSettings {
  base_currency: string;
  currencies: string[];
}

export type ProviderKind = "claude_code" | "anthropic" | "openai_compat";
export type DataLevel = "relative" | "full";
/** What "full detail" adds, in one place for every text that describes it (server: analyzer/persona.py). */
export const FULL_DETAIL_ITEMS = "amounts, quantities, costs, purchase dates, platforms and holding notes";
export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

export interface Provider {
  id: string;
  name: string;
  kind: ProviderKind;
  model: string;
  base_url: string;
  data_level: DataLevel;
  web_research: boolean;
  effort: Effort;
  is_default: boolean;
  builtin: boolean;
  local: boolean;
  key_hint: Nullable<string>;
  can_browse: boolean;
}

export interface ProviderInput {
  id?: string;
  name: string;
  kind: ProviderKind;
  model: string;
  base_url: string;
  data_level: DataLevel;
  web_research: boolean;
  effort: Effort;
  api_key?: string;
  clear_key: boolean;
}

export interface Preset {
  id: string;
  label: string;
  kind: ProviderKind;
  base_url: string;
  models: string[];
  local: boolean;
  needs_key: boolean;
}

export type Horizon = "short" | "medium" | "long";
export type Risk = "conservative" | "moderate" | "growth";

export interface Preferences {
  horizon: Horizon;
  risk: Risk;
  allow_short_term: boolean;
  /** Direct buy/sell/hold recommendations; confirmed opt-in, off by default. */
  allow_recommendations: boolean;
  language: "auto" | "en" | "hu";
  /** Country of residence as the user wrote it; empty = the analyst infers it and asks. */
  country: string;
  notes: string;
}

/** Ranges /api/portfolio/history accepts (server/main.py RANGE_DAYS). */
export type HistoryRange = "1W" | "1M" | "3M" | "6M" | "1Y" | "3Y" | "ALL";

export interface AiSettings {
  persona: string;
  is_default_persona: boolean;
  default_persona: string;
  preferences: Preferences;
  /** Standing rules sent with every advisor question (server: analyzer/persona.py normalize_rules). */
  rules: string[];
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface AdvisorShared {
  data_level: DataLevel;
  holdings: number;
  benchmarks: number;
  news_items: number;
  web_research: boolean;
  /** Holdings whose notes were sent (full detail only). */
  notes?: number;
}

export interface AdvisorSource {
  title?: string;
  url: string;
}

export interface AdvisorAnswer {
  text: string;
  model: string;
  sources: AdvisorSource[];
  provider: { id: string; name: string; kind: ProviderKind };
  shared: AdvisorShared;
}

export interface MarketItem {
  symbol: string;
  name: string;
  kind: string;
  last: number | null;
  as_of: Nullable<string>;
  change_1w: Nullable<number>;
  change_1m: Nullable<number>;
  change_3m: Nullable<number>;
  change_unit: "fraction" | "pp";
  vol_30d_annualized: Nullable<number>;
}

export interface NewsItem {
  source: string;
  title: string;
  summary: Nullable<string>;
  url: string;
  published: Nullable<string>;
  tickers: Nullable<string>;
}

export interface Research {
  market: MarketItem[];
  news: NewsItem[];
  feeds: { name: string; url: string }[];
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called once on any 401 from a request that did not opt out (the sign-in forms do). */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

interface RequestOptions {
  body?: unknown;
  form?: FormData;
  /** Do not treat 401 as "session expired" (wrong password on the login form). */
  keepOn401?: boolean;
  signal?: AbortSignal;
}

function detailMessage(status: number, payload: unknown): string {
  if (status === 413) return "That is too much to send at once.";
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: unknown; loc?: unknown } | undefined;
      const msg = first && typeof first.msg === "string" ? first.msg : "";
      const loc = first && Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1] ?? "") : "";
      const where = loc && loc !== "body" ? ` (${loc})` : "";
      return msg ? `Check the fields: ${msg}${where}` : "Check the fields.";
    }
  }
  if (status === 403) return "The server refused the request.";
  if (status >= 500) return `Server error (${status}).`;
  return `Request failed (${status}).`;
}

async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (method !== "GET") headers["X-Requested-With"] = "lia"; // CSRF guard: the server answers 403 without it
  if (opts.form) {
    body = opts.form; // the browser sets the multipart boundary
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(path, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the server; check that it is running");
  }

  let payload: unknown = null;
  const type = res.headers.get("content-type") ?? "";
  if (type.includes("application/json")) {
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }
  }

  if (!res.ok) {
    if (res.status === 401 && !opts.keepOn401) unauthorizedHandler?.();
    throw new ApiError(res.status, detailMessage(res.status, payload));
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>("GET", path, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>("POST", path, { ...opts, body }),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>("PUT", path, { ...opts, body }),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>("PATCH", path, { ...opts, body }),
  del: <T>(path: string, opts?: RequestOptions) => request<T>("DELETE", path, opts),
  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>("POST", path, { form });
  },
};

// ---- typed endpoints -------------------------------------------------------

export const endpoints = {
  authState: () => api.get<AuthState>("/api/auth/state"),
  login: (username: string, password: string) =>
    api.post<{ user: string }>("/api/auth/login", { username, password }, { keepOn401: true }),
  setup: (username: string, password: string, code: string) =>
    api.post<{ user: string }>("/api/auth/setup", { username, password, code }, { keepOn401: true }),
  logout: () => api.post<{ ok: boolean }>("/api/auth/logout"),
  /** Which "not financial advice" notice version the owner accepted (src/legal.ts holds the text). */
  disclaimer: () => api.get<{ accepted_version: string | null; accepted_at: string | null }>("/api/disclaimer"),
  acceptDisclaimer: (version: string) =>
    api.post<{ accepted_version: string | null; accepted_at: string | null }>("/api/disclaimer/accept", { version }),
  changePassword: (oldPassword: string, newPassword: string) =>
    api.post<{ ok: boolean }>("/api/auth/password", { old: oldPassword, new: newPassword }),

  portfolio: () => api.get<Snapshot>("/api/portfolio"),
  refresh: () => api.post<Snapshot>("/api/portfolio/refresh"),
  history: (range: HistoryRange) => api.get<HistoryResponse>(`/api/portfolio/history?range=${encodeURIComponent(range)}`),

  instruments: () => api.get<Instrument[]>("/api/instruments"),
  /** Update only: 404 if the holding was deleted meanwhile (the edit form must never re-create one). */
  saveInstrument: (body: InstrumentInput) => api.post<{ ok: boolean }>("/api/instruments?update=1", body),
  /** Create only: 409 if the ID exists (the add-holding wizard must never overwrite a holding). */
  createInstrument: (body: InstrumentInput) => api.post<{ ok: boolean }>("/api/instruments?create=1", body),
  deleteInstrument: (id: string, confirmPurchases: number) =>
    api.del<{ ok: boolean }>(`/api/instruments/${encodeURIComponent(id)}?confirm_purchases=${confirmPurchases}`),
  instrument: (id: string) => api.get<InstrumentDetail>(`/api/instruments/${encodeURIComponent(id)}`),

  addLot: (body: LotInput) => api.post<{ ok: boolean }>("/api/lots", body),
  platforms: () => api.get<string[]>("/api/platforms"),
  lookup: (q: string) => api.get<LookupResult[]>(`/api/lookup?q=${encodeURIComponent(q)}`),
  quote: (symbol: string) => api.get<QuotePreview>(`/api/quote?symbol=${encodeURIComponent(symbol)}`),
  deleteLot: (lotId: number, instrumentId: string) =>
    api.del<{ ok: boolean }>(`/api/lots/${lotId}?instrument_id=${encodeURIComponent(instrumentId)}`),

  settings: () => api.get<AppSettings>("/api/settings"),
  saveSettings: (baseCurrency: string) => api.put<AppSettings>("/api/settings", { base_currency: baseCurrency }),

  claudeStatus: () => api.get<ClaudeStatus>("/api/claude/status"),
  claudeLogin: () => api.post<{ url: string }>("/api/claude/login"),
  claudeCode: (code: string) => api.post<ClaudeStatus>("/api/claude/code", { code }),
  claudeLogout: () => api.post<ClaudeStatus>("/api/claude/logout"),

  aiPresets: () => api.get<Preset[]>("/api/ai/presets"),
  aiProviders: () => api.get<Provider[]>("/api/ai/providers"),
  /** "full" needs confirm_full: true (the server refuses it otherwise). */
  setDataLevel: (id: string, data_level: DataLevel, confirm_full: boolean) =>
    api.post<{ ok: boolean; data_level: DataLevel }>(`/api/ai/providers/${encodeURIComponent(id)}/data-level`, { data_level, confirm_full }),
  saveProvider: (body: ProviderInput) => api.post<{ id: string }>("/api/ai/providers", body),
  deleteProvider: (id: string) => api.del<{ ok: boolean }>(`/api/ai/providers/${encodeURIComponent(id)}`),
  makeDefaultProvider: (id: string) => api.post<{ ok: boolean }>(`/api/ai/providers/${encodeURIComponent(id)}/default`),
  testProvider: (id: string) => api.post<{ ok: boolean; model: string; reply: string }>(`/api/ai/providers/${encodeURIComponent(id)}/test`),
  aiSettings: () => api.get<AiSettings>("/api/ai/settings"),
  /** View preferences synced across devices (src/prefs.ts). PATCH sets or clears (null) only the keys sent. */
  prefs: () => api.get<Record<string, unknown>>("/api/prefs"),
  savePrefs: (changes: Record<string, unknown>) => api.patch<Record<string, unknown>>("/api/prefs", changes),
  saveAiSettings: (body: { persona?: string; preferences?: Partial<Preferences>; rules?: string[]; rules_expected?: string[] }) => api.put<AiSettings>("/api/ai/settings", body),
  /** expected_level: the level the conversation's answers were written at (409 if the model's level changed). */
  ask: (body: { question: string; history: ChatTurn[]; provider_id?: string; instrument_id?: string; expected_level?: DataLevel }) =>
    api.post<AdvisorAnswer>("/api/advisor/ask", body),
  research: () => api.get<Research>("/api/research"),
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
