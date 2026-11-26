// Holdings whose units sit on their record without a transaction (added before 2026-11-26, when units and cost
// started to come only from buys and sells). Each is turned into one buy, once, on the date the owner picks:
// POST /api/instruments/{id}/record-to-buy. Until then they keep counting as before.
import { useId, useState, type FormEvent } from "react";
import { endpoints, errorMessage, type Instrument } from "../api";
import { Alert } from "./Alert";
import { Card, CardHeader } from "./Card";
import { localISODate, parseDecimal, priceWithCcy, qty } from "../format";

function OldHolding({ item, onDone }: { item: Instrument; onDone: () => void }) {
  const uid = useId();
  const [date, setDate] = useState("");
  const [price, setPrice] = useState("");
  const [platform, setPlatform] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsPrice = !(item.avg_cost !== null && item.avg_cost > 0); // none, or 0 (older forms took it): ask
  // stored as quoted: in pence for a holding priced in GBp (price divisor 100), like every transaction
  const priceLabel = item.price_divisor !== 1 ? "Price per unit, as quoted (minor units)" : `Price per unit (${item.currency})`;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!date || date > localISODate()) return setError("Pick the date you bought it (not in the future).");
    const p = needsPrice ? parseDecimal(price) : null;
    if (needsPrice && (p === null || p <= 0)) return setError("Enter the price you paid per unit.");
    setBusy(true);
    try {
      await endpoints.recordToBuy(item.instrument_id, { buy_date: date, unit_price: p, platform: platform.trim() || null });
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="old-holding">
      <form className="form" onSubmit={submit} noValidate aria-label={`Turn ${item.name} into a buy`}>
        <div className="old-what">
          <b>{item.name}</b>
          <span className="sub">
            {qty(item.quantity)} units{needsPrice ? "" : ` at ${priceWithCcy(item.avg_cost, item.currency, true)}`}
          </span>
        </div>
        <div className="form-grid">
          <div className="field">
            <label htmlFor={`${uid}d`}>Date bought</label>
            <input id={`${uid}d`} type="date" value={date} max={localISODate()} onChange={(e) => setDate(e.target.value)} required />
          </div>
          {needsPrice && (
            <div className="field">
              <label htmlFor={`${uid}p`}>{priceLabel}</label>
              <input id={`${uid}p`} inputMode="decimal" autoComplete="off" value={price} onChange={(e) => setPrice(e.target.value)} required />
            </div>
          )}
          <div className="field">
            <label htmlFor={`${uid}pl`}>Platform, optional</label>
            <input id={`${uid}pl`} autoComplete="off" maxLength={60} value={platform} onChange={(e) => setPlatform(e.target.value)} placeholder="e.g. Revolut" />
          </div>
        </div>
        {error && <Alert tone="err">{error}</Alert>}
        <div className="btn-row">
          <button type="submit" className="btn small" disabled={busy} aria-busy={busy}>
            Save as a buy
          </button>
        </div>
      </form>
    </li>
  );
}

/** Nothing when every holding's units come from its transactions. */
export function OldHoldings({ items, onDone }: { items: Instrument[]; onDone: () => void }) {
  const old = items.filter((i) => i.record_only);
  if (!old.length) return null;
  return (
    <Card as="section" aria-labelledby="old-h">
      <CardHeader id="old-h" title={old.length === 1 ? "Turn a holding into a buy" : `Turn ${old.length} holdings into buys`} />
      <p className="sub">
        Units and cost now come only from buys and sells. {old.length === 1 ? "This holding has" : "These holdings have"} units
        without a buy: pick the date you bought {old.length === 1 ? "it" : "each"}, once. Return and the cost in your reporting
        currency use that date.
      </p>
      <ul className="old-list">
        {old.map((i) => (
          <OldHolding key={i.instrument_id} item={i} onDone={onDone} />
        ))}
      </ul>
    </Card>
  );
}
