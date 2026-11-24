import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Check, Search } from "lucide-react";
import { ApiError, endpoints, errorMessage, type Instrument, type LookupResult, type QuotePreview } from "../api";
import { COMMON_CURRENCIES } from "../currencies";
import { ASSET_TYPES, assetTypeOption, localISODate, money, parseDecimal, priceWithCcy, qty as fmtQty, typeLabel, type AssetType } from "../format";
import { useResource } from "../hooks";
import { useBase } from "../live";
import { Link } from "../router";
import { Alert } from "./Alert";
import { Card } from "./Card";
import { Select } from "./Select";

// Add a holding in small steps: what -> when -> price -> platform -> notes -> review.
// Saves the holding (POST /api/instruments) and its first purchase (POST /api/lots) at the end.

const STEPS = [
  { key: "what", label: "What", title: "What do you hold?" },
  { key: "when", label: "When", title: "When did you buy it, and how much?" },
  { key: "price", label: "Price", title: "What did you pay?" },
  { key: "where", label: "Platform", title: "Where do you hold it?" },
  { key: "notes", label: "Notes", title: "Anything to remember?" },
  { key: "review", label: "Review", title: "Check and save" },
] as const;

const EXAMPLE_PLATFORMS = ["Revolut", "OTP Bank", "Interactive Brokers", "Trading 212"];

interface Draft {
  manual: boolean;
  symbol: string;
  name: string;
  id: string;
  type: AssetType;
  currency: string;
  divisor: number;
  /** The unit prices are quoted in, e.g. "GBp" (pence) when divisor is 100. */
  quoteCcy: string;
  date: string;
  quantity: string;
  unitPrice: string;
  paid: string;
  paidCcy: string;
  /** The user picked the paid currency; until then it follows the reporting currency. */
  paidTouched: boolean;
  platform: string;
  notes: string;
  lotNote: string;
}

/** "VWCE.DE" -> "VWCE", "fund:HU0000728670" -> "HU0000728670"; unique against the ids already in use. */
function suggestId(symbol: string, taken: Set<string>): string {
  const base = (symbol.startsWith("fund:") ? symbol.slice(5) : symbol.split(/[.=^]/)[0] || symbol).toUpperCase().replace(/[^A-Z0-9_-]/g, "").slice(0, 24) || "HOLDING";
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}-${n}`;
  return id;
}

/** `existing` is null while the holdings list is loading or failed; the server still refuses a taken ID. */
export function AddHoldingWizard({ existing, onClose, onSaved }: { existing: readonly Instrument[] | null; onClose: () => void; onSaved: () => void }) {
  const uid = useId();
  const base = useBase();
  const taken = useMemo(() => new Set((existing ?? []).map((i) => i.instrument_id)), [existing]);
  const platforms = useResource(() => endpoints.platforms(), []);
  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);
  const [d, setD] = useState<Draft>({
    manual: false, symbol: "", name: "", id: "", type: "stock", currency: "", divisor: 1, quoteCcy: "",
    date: localISODate(), quantity: "", unitPrice: "", paid: "", paidCcy: base, paidTouched: false, platform: "", notes: "", lotNote: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedHolding, setSavedHolding] = useState(false); // the holding exists; a retry only re-sends the purchase
  const [done, setDone] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const lotTried = useRef(false); // a purchase request was sent at least once (its answer may have been lost)

  // Focus the step heading on open and on every step, so keyboard and screen-reader users land on it.
  useEffect(() => {
    heading.current?.focus();
  }, [step, done]);

  // The paid-in currency follows the reporting currency until the user picks one.
  useEffect(() => {
    setD((x) => (x.paidTouched || x.paidCcy === base ? x : { ...x, paidCcy: base }));
  }, [base]);

  const already = d.symbol ? (existing ?? []).find((i) => (i.market_symbol ?? "").toUpperCase() === d.symbol.toUpperCase()) : undefined;
  const qty = parseDecimal(d.quantity);
  const unit = parseDecimal(d.unitPrice);
  const paid = parseDecimal(d.paid);

  /** Why the current step cannot continue, or null. */
  const problem = (s: number): string | null => {
    if (s === 0 && !savedHolding) {
      if (!d.manual && !d.symbol) return "Pick a match from the search, or choose “Not in the list”.";
      if (!d.name.trim()) return "Give the holding a name.";
      if (!d.id.trim()) return "Give the holding a short ID.";
      if (taken.has(d.id.trim())) return `The ID ${d.id.trim()} is already used by another holding.`;
      if (!/^[A-Za-z]{3}$/.test(d.currency.trim())) return "Currency must be a 3-letter code such as EUR.";
    }
    if (s === 1) {
      if (!d.date || d.date > localISODate()) return "Pick the purchase date (not in the future).";
      if (qty === null || !(qty > 0)) return "Enter how many units you bought (more than 0).";
    }
    if (s === 2) {
      if ((unit === null || !(unit > 0)) && (paid === null || !(paid > 0))) return "Enter the price per unit, or what you paid in total.";
      if (unit !== null && unit < 0) return "The price cannot be negative.";
    }
    return null;
  };

  const next = (e: FormEvent) => {
    e.preventDefault();
    const p = problem(step);
    if (p) {
      setError(p);
      return;
    }
    setError(null);
    const to = Math.min(step + 1, STEPS.length - 1);
    setStep(to);
    setReached((r) => Math.max(r, to));
  };

  const save = async () => {
    for (let s = 0; s < STEPS.length - 1; s++) {
      const p = problem(s);
      if (p) {
        setStep(s);
        setError(p);
        return;
      }
    }
    setSaving(true);
    setError(null);
    const id = d.id.trim();
    let holdingSaved = savedHolding; // local: the state value in this closure would be stale
    try {
      if (!holdingSaved) {
        await endpoints.createInstrument({
          instrument_id: id, name: d.name.trim(), asset_type: d.type, quantity: 0, avg_cost: null,
          currency: d.currency.trim().toUpperCase(), market_symbol: d.symbol.trim() || null, price_divisor: d.divisor,
          notes: d.notes.trim() || null,
        });
        holdingSaved = true;
        setSavedHolding(true);
      }
      const retry = lotTried.current;
      lotTried.current = true;
      try {
        await endpoints.addLot({
          instrument_id: id, buy_date: d.date, quantity: qty as number, unit_price: unit ?? 0,
          paid_amount: paid !== null && paid > 0 ? paid : null, paid_currency: paid !== null && paid > 0 ? d.paidCcy : null,
          notes: d.lotNote.trim() || null, platform: d.platform.trim() || null,
        });
      } catch (err) {
        // 409 "identical purchase" on a retry: the earlier attempt did save it, only its answer was lost.
        if (!(retry && err instanceof ApiError && err.status === 409)) throw err;
      }
      setDone(true);
      onSaved();
    } catch (err) {
      setError(holdingSaved ? `${errorMessage(err)} The holding itself is saved; “Save” again only adds the purchase.` : errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  if (done) {
    return (
      <Card as="section" className="wizard" aria-labelledby={`${uid}h`}>
        <div className="wiz-done">
          <span className="wiz-done-icon" aria-hidden="true">
            <Check />
          </span>
          <h2 id={`${uid}h`} ref={heading} tabIndex={-1}>
            {d.name.trim()} is added
          </h2>
          <p className="sub">
            {d.symbol ? "Its prices and value fill in within a minute." : "It has no price symbol: import its prices on the Import page."} Add more purchases on its page.
          </p>
          <div className="btn-row">
            <Link className="btn" to={`/holding/${encodeURIComponent(d.id.trim())}`}>
              Open {d.id.trim()}
            </Link>
            <button type="button" className="btn ghost" onClick={onClose}>
              Done
            </button>
          </div>
        </div>
      </Card>
    );
  }

  const current = STEPS[step] as (typeof STEPS)[number];
  return (
    <Card as="section" className="wizard" aria-label="Add a holding">
      <ol className="wiz-steps" aria-label="Steps">
        {STEPS.map((s, i) => {
          const state = i === step ? "current" : i <= reached ? "done" : "todo";
          return (
            <li key={s.key} className={state}>
              <button type="button" aria-current={i === step ? "step" : undefined} disabled={i > reached || saving} onClick={() => { setError(null); setStep(i); }}>
                <span className="wiz-dot" aria-hidden="true">
                  {state === "done" && i !== step ? <Check /> : i + 1}
                </span>
                <span className="wiz-label">{s.label}</span>
                <span className="sr-only">{state === "done" ? " (done)" : state === "todo" ? " (not yet)" : ""}</span>
              </button>
            </li>
          );
        })}
      </ol>
      <p className="wiz-count">
        Step {step + 1} of {STEPS.length}
      </p>
      <h2 ref={heading} tabIndex={-1} className="wiz-title">
        {current.title}
      </h2>

      <form className="form wiz-body" onSubmit={step === STEPS.length - 1 ? (e) => { e.preventDefault(); void save(); } : next} noValidate>
        {step === 0 && existing === null && <Alert tone="info">Your holdings list is still loading; the ID is checked again when you save.</Alert>}
        {step === 0 && <WhatStep d={d} setD={setD} taken={taken} savedHolding={savedHolding} />}
        {step === 0 && already && (
          <Alert tone="warn">
            You already hold this as <b>{already.instrument_id}</b>. To record another purchase of it, use{" "}
            <Link className="btn ghost small" to={`/holding/${encodeURIComponent(already.instrument_id)}`}>
              its page
            </Link>{" "}
            instead.
          </Alert>
        )}

        {step === 1 && (
          <div className="form-grid">
            <div className="field">
              <label htmlFor={`${uid}date`}>Purchase date</label>
              <input id={`${uid}date`} type="date" value={d.date} max={localISODate()} onChange={(e) => set("date", e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor={`${uid}qty`}>Units bought</label>
              <input id={`${uid}qty`} inputMode="decimal" autoComplete="off" value={d.quantity} onChange={(e) => set("quantity", e.target.value)} placeholder="e.g. 12 or 0.5" aria-describedby={`${uid}qtyh`} />
              <span id={`${uid}qtyh`} className="help">
                Bought it several times? Enter the first purchase now and add the others on the holding's page.
              </span>
            </div>
          </div>
        )}

        {step === 2 && <PriceStep d={d} set={set} qty={qty} unit={unit} uid={uid} base={base} />}

        {step === 3 && (
          <div className="field">
            <label htmlFor={`${uid}plat`}>Platform, app or bank (optional)</label>
            <input id={`${uid}plat`} value={d.platform} maxLength={60} autoComplete="off" onChange={(e) => set("platform", e.target.value)} placeholder="e.g. Revolut" />
            <div className="chip-row" role="group" aria-label="Pick a platform">
              {(platforms.data?.length ? platforms.data : EXAMPLE_PLATFORMS).map((p) => (
                <button key={p} type="button" className="link-btn" aria-pressed={d.platform === p} onClick={() => set("platform", p)}>
                  {p}
                </button>
              ))}
            </div>
            <span className="help">Lets you see later where each purchase sits. Stored on this purchase only.</span>
          </div>
        )}

        {step === 4 && (
          <div className="form-grid">
            <div className="field wide">
              <label htmlFor={`${uid}notes`}>About the holding (optional)</label>
              <textarea id={`${uid}notes`} rows={3} maxLength={1000} value={d.notes} onChange={(e) => set("notes", e.target.value)} placeholder="Why you hold it, a target weight, a reminder…" />
            </div>
            <div className="field wide">
              <label htmlFor={`${uid}lnote`}>About this purchase (optional)</label>
              <input id={`${uid}lnote`} maxLength={500} value={d.lotNote} onChange={(e) => set("lotNote", e.target.value)} placeholder="e.g. monthly savings plan" />
            </div>
          </div>
        )}

        {step === 5 && <Review d={d} qty={qty} unit={unit} paid={paid} base={base} />}

        {error && <Alert tone="err">{error}</Alert>}

        <div className="btn-row wiz-actions">
          {step > 0 ? (
            <button type="button" className="btn ghost" disabled={saving} onClick={() => { setError(null); setStep(step - 1); }}>
              <ArrowLeft aria-hidden="true" />
              Back
            </button>
          ) : (
            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                if (savedHolding) onSaved(); // the holding exists even though its purchase failed
                onClose();
              }}
            >
              Cancel
            </button>
          )}
          {step < STEPS.length - 1 ? (
            <button type="submit" className="btn">
              {step === 3 && !d.platform.trim() ? "Skip" : step === 4 && !d.notes.trim() && !d.lotNote.trim() ? "Skip" : "Next"}
              <ArrowRight aria-hidden="true" />
            </button>
          ) : (
            <button type="submit" className="btn" disabled={saving}>
              {saving && <span className="spinner" aria-hidden="true" />}
              {saving ? "Saving…" : savedHolding ? "Save the purchase" : "Save holding"}
            </button>
          )}
        </div>
      </form>
    </Card>
  );
}

function WhatStep({ d, setD, taken, savedHolding }: { d: Draft; setD: (f: (x: Draft) => Draft) => void; taken: Set<string>; savedHolding: boolean }) {
  const uid = useId();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<LookupResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [quote, setQuote] = useState<QuotePreview | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);

  // Search as you type (debounced); the latest query wins.
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2) {
      setResults(null);
      setSearchError(null);
      setSearching(false);
      return;
    }
    let alive = true;
    const t = window.setTimeout(async () => {
      setSearching(true);
      try {
        const r = await endpoints.lookup(text);
        if (alive) {
          setResults(r);
          setSearchError(null);
        }
      } catch (err) {
        if (alive) setSearchError(errorMessage(err));
      } finally {
        if (alive) setSearching(false);
      }
    }, 350);
    return () => {
      alive = false;
      window.clearTimeout(t);
      setSearching(false);
    };
  }, [q]);

  const quoteReq = useRef(0);
  const checkPrice = async (symbol: string) => {
    const mine = ++quoteReq.current;
    setQuote(null);
    setQuoteError(null);
    try {
      const p = await endpoints.quote(symbol);
      if (mine !== quoteReq.current) return; // a newer pick is being priced
      setQuote(p);
      setD((x) => (x.symbol === symbol
        ? { ...x, currency: p.currency ?? x.currency, divisor: p.divisor || 1, quoteCcy: p.quote_currency ?? p.currency ?? "" }
        : x));
    } catch (err) {
      if (mine === quoteReq.current) setQuoteError(errorMessage(err));
    }
  };

  const pick = (r: LookupResult) => {
    setD((x) => ({ ...x, manual: false, symbol: r.symbol, name: r.name, type: r.type, id: suggestId(r.symbol, taken), currency: "", divisor: 1, quoteCcy: "" }));
    void checkPrice(r.symbol);
  };

  const locked = savedHolding; // after the holding is saved only the purchase can be retried
  const showFields = d.manual || !!d.symbol;
  return (
    <>
      {!locked && (
        <div className="field">
          <label htmlFor={`${uid}q`}>Search by name, ticker or ISIN</label>
          <div className="wiz-search">
            {/* While a search runs the magnifier turns into a spinner: nothing below moves. */}
            {searching ? <span className="spinner" aria-hidden="true" /> : <Search aria-hidden="true" />}
            <input id={`${uid}q`} type="search" value={q} autoComplete="off" spellCheck={false} placeholder="e.g. Vanguard All-World, AAPL, IE00BK5BQT80" onChange={(e) => setQ(e.target.value)} aria-describedby={`${uid}qh`} />
          </div>
          <span id={`${uid}qh`} className="help">
            Searches Yahoo Finance (only what you type is sent). Hungarian fund ISINs (HU…) are looked up at OTP Alapkezelő / BAMOSZ.
          </span>
        </div>
      )}

      {!locked && (
        <div className="wiz-results">
          <p className="sr-only" aria-live="polite">
            {searching ? "Searching" : results ? `${results.length} ${results.length === 1 ? "match" : "matches"}` : ""}
          </p>
          {searchError && <Alert tone="warn">{searchError} You can still enter it manually.</Alert>}
          {results && !searching && results.length === 0 && <p className="sub">No match for “{q.trim()}”. Enter it manually below.</p>}
          {results && results.length > 0 && (
            <ul className="wiz-list" aria-label="Matches">
              {results.map((r) => (
                <li key={r.symbol}>
                  <button type="button" className="wiz-option" aria-pressed={!d.manual && d.symbol === r.symbol} onClick={() => pick(r)}>
                    <b>{r.name}</b>
                    <span>
                      {r.symbol} · {typeLabel(r.type)}
                      {r.exchange ? ` · ${r.exchange}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            className="link-btn"
            aria-pressed={d.manual}
            onClick={() => {
              quoteReq.current += 1;
              setQuote(null);
              setQuoteError(null);
              setD((x) => ({ ...x, manual: true, symbol: "", name: x.name || q.trim(), id: x.id || suggestId(q.trim() || "HOLDING", taken), divisor: 1, quoteCcy: "" }));
            }}
          >
            Not in the list: enter it manually
          </button>
        </div>
      )}

      {showFields && (
        <div className="form-grid wiz-fields">
          <div className="field wide">
            <label htmlFor={`${uid}name`}>Name</label>
            <input id={`${uid}name`} value={d.name} maxLength={200} onChange={(e) => setD((x) => ({ ...x, name: e.target.value }))} disabled={locked} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}id`}>Short ID</label>
            <input id={`${uid}id`} value={d.id} maxLength={64} autoComplete="off" spellCheck={false} onChange={(e) => setD((x) => ({ ...x, id: e.target.value.toUpperCase() }))} disabled={locked} aria-describedby={`${uid}idh`} />
            <span id={`${uid}idh`} className="help">
              Your label for it, used in imports. Cannot be changed later.
            </span>
          </div>
          <div className="field">
            <label htmlFor={`${uid}type`}>Type</label>
            <Select id={`${uid}type`} value={d.type} disabled={locked} onValueChange={(v) => setD((x) => ({ ...x, type: v as AssetType }))} options={ASSET_TYPES.map((t) => ({ value: t, label: assetTypeOption(t) }))} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}ccy`}>Currency it trades in</label>
            <input id={`${uid}ccy`} value={d.currency} maxLength={3} autoComplete="off" spellCheck={false} placeholder="EUR" onChange={(e) => setD((x) => ({ ...x, currency: e.target.value.toUpperCase() }))} disabled={locked} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}sym`}>Price symbol {d.manual ? "(optional)" : ""}</label>
            <input
              id={`${uid}sym`}
              value={d.symbol}
              maxLength={64}
              autoComplete="off"
              spellCheck={false}
              placeholder="e.g. VWCE.DE or fund:HU0000728670"
              onChange={(e) => setD((x) => ({ ...x, symbol: e.target.value.trim() }))}
              onBlur={() => d.manual && d.symbol && void checkPrice(d.symbol)}
              disabled={locked || !d.manual}
              aria-describedby={`${uid}symh`}
            />
            <span id={`${uid}symh`} className="help">
              {d.manual ? "Leave empty if it has no public price (you can import prices instead)." : "From the search result."}
            </span>
          </div>
          <div className="wide wiz-quote" aria-live="polite">
            {quote && (
              <span>
                Latest price: <b>{priceWithCcy(quote.price, quote.currency)}</b>
                {quote.as_of ? ` · ${quote.as_of.slice(0, 10)}` : ""} · {quote.source}
                {d.divisor !== 1 ? ` · quoted in minor units, stored as ${d.currency} ÷ ${d.divisor}` : ""}
              </span>
            )}
            {quoteError && <span className="muted">No price found yet ({quoteError}). You can still save it.</span>}
          </div>
        </div>
      )}
    </>
  );
}

function PriceStep({ d, set, qty, unit, uid, base }: { d: Draft; set: <K extends keyof Draft>(k: K, v: Draft[K]) => void; qty: number | null; unit: number | null; uid: string; base: string }) {
  const ccys = Array.from(new Set([base, d.currency.toUpperCase(), ...COMMON_CURRENCIES].filter(Boolean)));
  const total = qty !== null && unit !== null ? (qty * unit) / d.divisor : null;
  return (
    <div className="form-grid">
      <div className="field">
        <label htmlFor={`${uid}unit`}>
          Price per unit ({d.divisor !== 1 ? `as quoted, in minor units: ${d.divisor} = 1 ${d.currency}` : d.currency || "its currency"})
        </label>
        <input id={`${uid}unit`} inputMode="decimal" autoComplete="off" value={d.unitPrice} onChange={(e) => set("unitPrice", e.target.value)} placeholder="e.g. 131.20" aria-describedby={`${uid}unith`} />
        <span id={`${uid}unith`} className="help">
          {total !== null && Number.isFinite(total) ? `≈ ${money(total, d.currency || "EUR")} in total, before fees.` : "As shown in your broker's confirmation."}
        </span>
      </div>
      <div className="field">
        <label htmlFor={`${uid}paid`}>Paid in total, fees included (optional)</label>
        <div className="amount-row">
          <input id={`${uid}paid`} inputMode="decimal" autoComplete="off" value={d.paid} onChange={(e) => set("paid", e.target.value)} aria-describedby={`${uid}paidh`} />
          <label className="sr-only" htmlFor={`${uid}paidc`}>
            Currency paid in
          </label>
          <Select id={`${uid}paidc`} value={d.paidCcy} onValueChange={(v) => { set("paidCcy", v); set("paidTouched", true); }} options={ccys.map((c) => ({ value: c, label: c }))} />
        </div>
        <span id={`${uid}paidh`} className="help">
          Makes profit and loss match your broker exactly, e.g. what left your HUF account.
        </span>
      </div>
    </div>
  );
}

function Review({ d, qty, unit, paid, base }: { d: Draft; qty: number | null; unit: number | null; paid: number | null; base: string }) {
  const rows: [string, string][] = [
    ["Holding", `${d.name.trim()} (${d.id.trim()})`],
    ["Type", typeLabel(d.type)],
    ["Currency", d.currency.toUpperCase() + (d.divisor !== 1 ? ` (prices ÷ ${d.divisor})` : "")],
    ["Price symbol", d.symbol || "none (no automatic prices)"],
    ["Bought", `${qty !== null ? fmtQty(qty) : "?"} ${qty === 1 ? "unit" : "units"} on ${d.date}`],
    ["Price per unit", unit !== null && unit > 0
      ? d.divisor !== 1
        ? `${priceWithCcy(unit, d.quoteCcy || "minor units")} (= ${priceWithCcy(unit / d.divisor, d.currency)})`
        : priceWithCcy(unit, d.currency)
      : "not given"],
    ["Paid in total", paid !== null && paid > 0 ? money(paid, d.paidCcy || base) : "not given"],
    ["Platform", d.platform.trim() || "not given"],
  ];
  if (d.notes.trim()) rows.push(["Note", d.notes.trim()]);
  if (d.lotNote.trim()) rows.push(["Purchase note", d.lotNote.trim()]);
  return (
    <dl className="wiz-review">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
