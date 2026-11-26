import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { COMMON_CURRENCIES } from "../currencies";
import { endpoints, errorMessage, type Lot, type Side } from "../api";
import { ccyLabel, localISODate, money, parseDecimal, priceWithCcy, qty, signClass, signedMoney } from "../format";
import { useSortFilter } from "../useSortFilter";
import { Alert } from "./Alert";
import { DataTable, type Column, type PhoneSortOption } from "./DataTable";
import { Card, CardHeader } from "./Card";
import { Segmented } from "./Segmented";
import { Select } from "./Select";

interface PurchasesProps {
  instrumentId: string;
  currency: string;
  /** The reporting currency: the default for the amount paid or received, and what realized P/L is shown in. */
  base: string;
  /** Every transaction of this holding, buys and sells (the component keeps its old name). */
  lots: Lot[];
  onChanged: () => void;
}

const SIDES = [
  { value: "buy", label: "Buy" },
  { value: "sell", label: "Sell" },
] as const;

const PHONE_SORT: readonly PhoneSortOption[] = [
  { key: "date", label: "Date", dir: "desc" },
  { key: "quantity", label: "Quantity", dir: "desc" },
  { key: "unit_price", label: "Unit price", dir: "desc" },
  { key: "paid", label: "Paid / received", dir: "desc" },
  { key: "realized", label: "Realized P/L", dir: "desc" },
];

function paidText(l: Lot): string {
  return l.paid_amount !== null && l.paid_amount !== undefined ? money(l.paid_amount, l.paid_currency || "HUF") : "";
}

/** "purchase" / "sale": how a transaction is named in a sentence. */
const noun = (side: Side) => (side === "sell" ? "sale" : "purchase");

export function Purchases({ instrumentId, currency, base, lots, onChanged }: PurchasesProps) {
  const [confirming, setConfirming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // The add form folds away behind a button once there is a transaction; with none it is the next step.
  const [adding, setAdding] = useState(lots.length === 0);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const closed = useRef(false);
  useEffect(() => {
    if (!adding && closed.current) toggleRef.current?.focus(); // back to the button after Cancel
  }, [adding]);

  const sf = useSortFilter<Lot>({
    id: `purchases`,
    rows: lots,
    saveFilter: false, // one table per holding: the sort is shared, a filter stays on its own page
    fields: {
      date: { type: "text", value: (l) => l.buy_date },
      type: { type: "text", value: (l) => l.side },
      quantity: { type: "number", value: (l) => l.quantity },
      unit_price: { type: "number", value: (l) => l.unit_price },
      paid: { type: "number", value: (l) => l.paid_amount },
      realized: { type: "number", value: (l) => l.realized_base },
    },
    search: (l) => [l.buy_date, l.side, qty(l.quantity), priceWithCcy(l.unit_price, currency, true), paidText(l), l.platform, l.notes].filter(Boolean).join(" "),
    defaultSort: { key: "date", dir: "desc" },
  });

  const remove = async (lot: Lot) => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await endpoints.deleteLot(lot.lot_id, instrumentId);
      setConfirming(null);
      onChanged();
    } catch (err) {
      setDeleteError(errorMessage(err));
    } finally {
      setDeleting(false);
    }
  };

  const columns: Column<Lot>[] = [
    {
      key: "date",
      header: "Date",
      sortKey: "date",
      cell: (l) => (
        <span>
          <b>{l.buy_date}</b>
          {l.notes && <small className="cell-sub muted">{l.notes}</small>}
        </span>
      ),
    },
    {
      key: "type",
      header: "Type",
      sortKey: "type",
      cell: (l) => <span className={l.side === "sell" ? "badge accent" : "badge"}>{l.side === "sell" ? "Sell" : "Buy"}</span>,
    },
    { key: "quantity", header: "Quantity", sortKey: "quantity", align: "right", cell: (l) => qty(l.quantity) },
    { key: "unit_price", header: "Unit price", sortKey: "unit_price", align: "right", cell: (l) => priceWithCcy(l.unit_price, currency, true) },
    { key: "paid", header: "Paid / received", label: "Amount", sortKey: "paid", align: "right", cell: (l) => paidText(l) || "—" },
    {
      key: "realized",
      header: "Realized P/L",
      term: "realizedPL",
      sortKey: "realized",
      align: "right",
      // Sells only; a sale whose figures are incomplete shows a dash, the holding's data check says why.
      cell: (l) => (l.side === "sell" ? <span className={signClass(l.realized_base)}>{signedMoney(l.realized_base, base)}</span> : "—"),
    },
    { key: "platform", header: "Platform", cell: (l) => l.platform || "—" },
    {
      key: "actions",
      header: "Actions",
      hideHeader: true,
      className: "acts",
      cell: (l) =>
        confirming === l.lot_id ? null : (
          <button
            type="button"
            className="btn ghost small"
            onClick={() => {
              setDeleteError(null);
              setConfirming(l.lot_id);
            }}
            aria-label={`Delete ${noun(l.side)} of ${qty(l.quantity)} on ${l.buy_date}`}
          >
            Delete
          </button>
        ),
    },
  ];

  return (
    <Card as="section" aria-labelledby="purchases-h">
      <CardHeader id="purchases-h" title="Transactions" aside={<span className="sub">{lots.length === 0 ? "none yet" : `${lots.length} recorded`}</span>} />
      {lots.length === 0 ? (
        <div className="empty">
          <b>No transactions recorded yet</b>
          Add each buy and sell (date, units, price, what you paid or received) to see P/L from your own figures. Until then the average cost from your holding record is used.
        </div>
      ) : (
        <DataTable
          sf={sf}
          columns={columns}
          rowKey={(l) => l.lot_id}
          label="transactions"
          variant="stack"
          className="dt-lots"
          phoneSort={PHONE_SORT}
          expanded={(l) =>
            confirming === l.lot_id ? (
              <div className="confirm" role="group" aria-label="Confirm delete">
                <span>
                  Delete {noun(l.side)} of {qty(l.quantity)} on {l.buy_date}?
                </span>
                <button type="button" className="btn danger small" onClick={() => remove(l)} disabled={deleting}>
                  Yes, delete
                </button>
                <button type="button" className="btn ghost small" onClick={() => setConfirming(null)} disabled={deleting} autoFocus>
                  No
                </button>
              </div>
            ) : null
          }
        />
      )}
      {deleteError && (
        <div style={{ marginTop: 10 }}>
          <Alert tone="err">{deleteError}</Alert>
        </div>
      )}
      {adding ? (
        <>
          <hr className="divider" />
          <AddPurchase
            instrumentId={instrumentId}
            currency={currency}
            base={base}
            onAdded={onChanged}
            canSell={lots.some((l) => l.side === "buy")}
            focusFirst={lots.length > 0}
            onCancel={
              lots.length > 0
                ? () => {
                    closed.current = true;
                    setAdding(false);
                  }
                : undefined
            }
          />
        </>
      ) : (
        <div className="btn-row" style={{ marginTop: 12 }}>
          <button ref={toggleRef} type="button" className="btn ghost small" onClick={() => setAdding(true)} aria-expanded={false}>
            <Plus aria-hidden="true" />
            Add transaction
          </button>
        </div>
      )}
    </Card>
  );
}

interface AddPurchaseProps {
  instrumentId: string;
  currency: string;
  base: string;
  onAdded: () => void;
  /** Opened from the "Add transaction" button: start in the first control. */
  focusFirst?: boolean;
  /** Folds the form away again (absent while it is the only way to add the first transaction). */
  onCancel?: () => void;
  /** A sale needs a recorded purchase to sell from: without one only buys can be added. */
  canSell: boolean;
}

function AddPurchase({ instrumentId, currency, base, onAdded, focusFirst = false, onCancel, canSell }: AddPurchaseProps) {
  const uid = useId();
  const firstRef = useRef<HTMLInputElement>(null);
  const sideRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // The Buy/Sell choice comes first: a keyboard or screen-reader user must not record a sale as a buy by
    // landing on the date. Focus its pressed segment; without a toggle (no purchase yet) the date.
    if (focusFirst) (sideRef.current?.querySelector<HTMLElement>('button[aria-pressed="true"]') ?? firstRef.current)?.focus();
  }, [focusFirst]);
  const [sideChoice, setSide] = useState<Side>("buy");
  const side: Side = canSell ? sideChoice : "buy";
  const [date, setDate] = useState(localISODate());
  const [quantity, setQuantity] = useState("");
  const [unitPrice, setUnitPrice] = useState("");
  const [paid, setPaid] = useState("");
  const [paidCcy, setPaidCcy] = useState(base);
  const [platform, setPlatform] = useState("");
  const [touched, setTouched] = useState(false);
  // Follow the display currency until the user picks one.
  useEffect(() => {
    if (!touched) setPaidCcy(base);
  }, [base, touched]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const label = ccyLabel(currency) || "holding currency";
  const amountLabel = side === "sell" ? "Amount received" : "Amount paid";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setDone(null);
    const q = parseDecimal(quantity);
    const p = parseDecimal(unitPrice);
    const c = parseDecimal(paid);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return setError("Pick the date.");
    if (q === null || q <= 0) return setError("Quantity must be a number above zero.");
    if (p === null || p < 0) return setError(`Enter the unit price in ${label}.`);
    if (paid.trim() !== "" && (c === null || c < 0)) return setError(`${amountLabel} must be a number, or leave it empty.`);
    setBusy(true);
    try {
      await endpoints.addLot({ instrument_id: instrumentId, side, buy_date: date, quantity: q, unit_price: p, paid_amount: c, paid_currency: c === null ? null : paidCcy, notes: null, platform: platform.trim() || null });
      setDone(side === "sell" ? `Added a sale of ${qty(q)} on ${date}.` : `Added a purchase of ${qty(q)} on ${date}.`);
      setQuantity("");
      setUnitPrice("");
      setPaid("");
      onAdded();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-labelledby={`${uid}h`}>
      <h3 id={`${uid}h`} style={{ fontSize: 14 }}>
        Add transaction
      </h3>
      {canSell ? (
        <div ref={sideRef}>
          <Segmented label="Transaction type" options={SIDES} value={side} onChange={setSide} />
        </div>
      ) : (
        <p className="help">This holding's units come from its holding record. Record its purchases first, then the sale.</p>
      )}
      <div className="form-grid">
        <div className="field">
          <label htmlFor={`${uid}d`}>Date</label>
          <input ref={firstRef} id={`${uid}d`} type="date" value={date} max={localISODate()} onChange={(e) => setDate(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor={`${uid}q`}>Quantity</label>
          <input id={`${uid}q`} inputMode="decimal" autoComplete="off" value={quantity} onChange={(e) => setQuantity(e.target.value)} placeholder="e.g. 12,5" required />
        </div>
        <div className="field">
          <label htmlFor={`${uid}p`}>Unit price ({label})</label>
          <input id={`${uid}p`} inputMode="decimal" autoComplete="off" value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} placeholder="price per unit" required />
        </div>
        <div className="field">
          <label htmlFor={`${uid}c`}>{amountLabel} (optional)</label>
          <div className="amount-row">
            <input id={`${uid}c`} inputMode="decimal" autoComplete="off" value={paid} onChange={(e) => setPaid(e.target.value)} aria-describedby={`${uid}ch`} />
            <label className="sr-only" htmlFor={`${uid}cc`}>
              {side === "sell" ? "Currency received in" : "Currency paid in"}
            </label>
            <Select
              id={`${uid}cc`}
              value={paidCcy}
              onValueChange={(v) => {
                setTouched(true);
                setPaidCcy(v);
              }}
              options={Array.from(new Set([base, currency.toUpperCase(), ...COMMON_CURRENCIES])).map((c) => ({ value: c, label: c }))}
            />
          </div>
          <span id={`${uid}ch`} className="help">
            {side === "sell" ? "After fees, as credited to you, to match your broker exactly." : "Include fees to match your broker exactly."}
          </span>
        </div>
        <div className="field">
          <label htmlFor={`${uid}pl`}>Platform, app or bank (optional)</label>
          <input id={`${uid}pl`} value={platform} maxLength={60} autoComplete="off" onChange={(e) => setPlatform(e.target.value)} placeholder="e.g. Revolut" />
        </div>
      </div>
      {error && <Alert tone="err">{error}</Alert>}
      {done && <Alert tone="ok">{done}</Alert>}
      <div className="btn-row">
        <button type="submit" className="btn" disabled={busy}>
          {busy && <span className="spinner" aria-hidden="true" />}
          {busy ? "Adding…" : side === "sell" ? "Add sell" : "Add buy"}
        </button>
        {onCancel && (
          <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
