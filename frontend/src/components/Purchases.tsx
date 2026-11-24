import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { COMMON_CURRENCIES } from "../currencies";
import { endpoints, errorMessage, type Lot } from "../api";
import { ccyLabel, localISODate, money, parseDecimal, priceWithCcy, qty } from "../format";
import { useSortFilter } from "../useSortFilter";
import { Alert } from "./Alert";
import { DataTable, type Column, type PhoneSortOption } from "./DataTable";
import { Card, CardHeader } from "./Card";
import { Select } from "./Select";

interface PurchasesProps {
  instrumentId: string;
  currency: string;
  /** The reporting currency: the default for "paid in total". */
  base: string;
  lots: Lot[];
  onChanged: () => void;
}

const PHONE_SORT: readonly PhoneSortOption[] = [
  { key: "date", label: "Date", dir: "desc" },
  { key: "quantity", label: "Quantity", dir: "desc" },
  { key: "unit_price", label: "Unit price", dir: "desc" },
  { key: "paid", label: "Paid", dir: "desc" },
];

function paidText(l: Lot): string {
  return l.paid_amount !== null && l.paid_amount !== undefined ? money(l.paid_amount, l.paid_currency || "HUF") : "";
}

export function Purchases({ instrumentId, currency, base, lots, onChanged }: PurchasesProps) {
  const [confirming, setConfirming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // The add form folds away behind a button once there is a purchase; with none it is the next step.
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
      quantity: { type: "number", value: (l) => l.quantity },
      unit_price: { type: "number", value: (l) => l.unit_price },
      paid: { type: "number", value: (l) => l.paid_amount },
    },
    search: (l) => [l.buy_date, qty(l.quantity), priceWithCcy(l.unit_price, currency, true), paidText(l), l.platform, l.notes].filter(Boolean).join(" "),
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
    { key: "quantity", header: "Quantity", sortKey: "quantity", align: "right", cell: (l) => qty(l.quantity) },
    { key: "unit_price", header: "Unit price", sortKey: "unit_price", align: "right", cell: (l) => priceWithCcy(l.unit_price, currency, true) },
    { key: "paid", header: "Paid", sortKey: "paid", align: "right", cell: (l) => paidText(l) || "—" },
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
            aria-label={`Delete purchase of ${qty(l.quantity)} on ${l.buy_date}`}
          >
            Delete
          </button>
        ),
    },
  ];

  return (
    <Card as="section" aria-labelledby="purchases-h">
      <CardHeader id="purchases-h" title="Purchases" aside={<span className="sub">{lots.length === 0 ? "none yet" : `${lots.length} recorded`}</span>} />
      {lots.length === 0 ? (
        <div className="empty">
          <b>No purchases recorded yet</b>
          Add each buy (date, units, price, what you paid) to see P/L per lot. Until then the average cost from your holding record is used.
        </div>
      ) : (
        <DataTable
          sf={sf}
          columns={columns}
          rowKey={(l) => l.lot_id}
          label="purchases"
          variant="stack"
          className="dt-lots"
          phoneSort={PHONE_SORT}
          expanded={(l) =>
            confirming === l.lot_id ? (
              <div className="confirm" role="group" aria-label="Confirm delete">
                <span>
                  Delete purchase of {qty(l.quantity)} on {l.buy_date}?
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
            Add purchase
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
  /** Opened from the "Add purchase" button: start in the first field. */
  focusFirst?: boolean;
  /** Folds the form away again (absent while it is the only way to add the first purchase). */
  onCancel?: () => void;
}

function AddPurchase({ instrumentId, currency, base, onAdded, focusFirst = false, onCancel }: AddPurchaseProps) {
  const uid = useId();
  const firstRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (focusFirst) firstRef.current?.focus();
  }, [focusFirst]);
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

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setDone(null);
    const q = parseDecimal(quantity);
    const p = parseDecimal(unitPrice);
    const c = parseDecimal(paid);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return setError("Pick the purchase date.");
    if (q === null || q <= 0) return setError("Quantity must be a number above zero.");
    if (p === null || p < 0) return setError(`Enter the unit price in ${label}.`);
    if (paid.trim() !== "" && (c === null || c < 0)) return setError("Paid in total must be a number, or leave it empty.");
    setBusy(true);
    try {
      await endpoints.addLot({ instrument_id: instrumentId, buy_date: date, quantity: q, unit_price: p, paid_amount: c, paid_currency: c === null ? null : paidCcy, notes: null, platform: platform.trim() || null });
      setDone(`Added ${qty(q)} bought on ${date}.`);
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
        Add purchase
      </h3>
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
          <label htmlFor={`${uid}c`}>Paid in total (optional)</label>
          <div className="amount-row">
            <input id={`${uid}c`} inputMode="decimal" autoComplete="off" value={paid} onChange={(e) => setPaid(e.target.value)} aria-describedby={`${uid}ch`} />
            <label className="sr-only" htmlFor={`${uid}cc`}>
              Currency paid in
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
            Include fees to match your broker exactly.
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
          {busy ? "Adding…" : "Add purchase"}
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
