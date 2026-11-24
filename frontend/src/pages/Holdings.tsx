import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type RefObject } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { ApiError, endpoints, errorMessage, type Instrument } from "../api";
import { Alert } from "../components/Alert";
import { Card, CardHeader } from "../components/Card";
import { BlockSkeleton } from "../components/Skeleton";
import { AddHoldingWizard } from "../components/AddHoldingWizard";
import { Select } from "../components/Select";
import { DataTable, type Column, type PhoneSortOption } from "../components/DataTable";
import { useSortFilter } from "../useSortFilter";
import { ASSET_TYPES, assetTypeOption, isNum, parseDecimal, price, qty, typeLabel } from "../format";
import { useResource, useTitle } from "../hooks";
import { Link, navigate, useSearch } from "../router";
import { useLive } from "../live";

interface FormState {
  instrument_id: string;
  name: string;
  asset_type: string;
  quantity: string;
  avg_cost: string;
  currency: string;
  market_symbol: string;
  price_divisor: string;
  notes: string;
}

const EMPTY: FormState = {
  instrument_id: "",
  name: "",
  asset_type: "stock",
  quantity: "",
  avg_cost: "",
  currency: "",
  market_symbol: "",
  price_divisor: "1",
  notes: "",
};

function toForm(i: Instrument): FormState {
  return {
    instrument_id: i.instrument_id,
    name: i.name,
    asset_type: i.asset_type,
    quantity: String(i.quantity),
    avg_cost: i.avg_cost === null ? "" : String(i.avg_cost),
    currency: i.currency,
    market_symbol: i.market_symbol ?? "",
    price_divisor: String(i.price_divisor),
    notes: i.notes ?? "",
  };
}

const PHONE_SORT: readonly PhoneSortOption[] = [
  { key: "id", label: "ID", dir: "asc" },
  { key: "name", label: "Name", dir: "asc" },
  { key: "type", label: "Type", dir: "asc" },
  { key: "quantity", label: "Quantity", dir: "desc" },
  { key: "avg_cost", label: "Avg cost", dir: "desc" },
  { key: "currency", label: "Currency", dir: "asc" },
  { key: "symbol", label: "Symbol", dir: "asc" },
];

/** What the delete flow is doing for one holding. */
type DeleteState =
  | { id: string; step: "ask" }
  | { id: string; step: "purchases"; count: number; message: string };

export function HoldingsPage() {
  useTitle("Holdings");
  const list = useResource(() => endpoints.instruments(), []);
  const [editing, setEditing] = useState<{ mode: "add" } | { mode: "edit"; id: string } | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [delState, setDelState] = useState<DeleteState | null>(null);
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const addButton = useRef<HTMLButtonElement>(null);

  const open = (mode: "add" | { edit: Instrument }) => {
    if (mode === "add") {
      setForm(EMPTY);
      setEditing({ mode: "add" });
    } else {
      setForm(toForm(mode.edit));
      setEditing({ mode: "edit", id: mode.edit.instrument_id });
    }
  };

  useEffect(() => {
    if (editing?.mode === "edit") {
      formRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
      formRef.current?.querySelector<HTMLInputElement>("input:not(:disabled)")?.focus({ preventScroll: true });
    }
  }, [editing]);

  // /holdings?add=1 (e.g. from the empty Overview) opens the add-holding wizard at once.
  const search = useSearch();
  useEffect(() => {
    if (new URLSearchParams(search).get("add") === "1") {
      setEditing({ mode: "add" });
      navigate("/holdings", { replace: true });
    }
  }, [search]);

  const remove = async (id: string, confirmPurchases: number) => {
    setDelBusy(true);
    setDelError(null);
    try {
      await endpoints.deleteInstrument(id, confirmPurchases);
      setDelState(null);
      list.reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const m = /confirm_purchases=(\d+)/.exec(err.message);
        if (m) {
          setDelState({ id, step: "purchases", count: Number(m[1]), message: err.message });
          setDelBusy(false);
          return;
        }
      }
      setDelError(errorMessage(err));
    } finally {
      setDelBusy(false);
    }
  };

  // Quantity and average cost as the portfolio counts them: from the purchases when there are
  // any (the live snapshot), else the holding record's own figures.
  const live = useLive();
  const rowsById = useMemo(() => new Map((live.snapshot?.rows ?? []).map((r) => [r.id, r])), [live.snapshot]);
  // Until the first snapshot arrives (a few seconds after a server start) the purchase-based
  // figures are unknown: null shows "…" rather than the holding record's 0.
  const pending = live.snapshot === null && !live.loaded; // a failed load falls back to the record
  const qtyOf = (i: Instrument): number | null => {
    if (pending) return null;
    const q = rowsById.get(i.instrument_id)?.quantity;
    return isNum(q) ? q : i.quantity;
  };
  const avgOf = (i: Instrument): number | null => {
    if (pending) return null;
    const r = rowsById.get(i.instrument_id);
    return r ? r.avg_cost : i.avg_cost;
  };

  const items = list.data ?? [];
  const sf = useSortFilter<Instrument>({
    id: "holdings-manage",
    rows: items,
    fields: {
      id: { type: "text", value: (i) => i.instrument_id },
      name: { type: "text", value: (i) => i.name },
      type: { type: "text", value: (i) => typeLabel(i.asset_type) },
      quantity: { type: "number", value: qtyOf },
      avg_cost: { type: "number", value: avgOf },
      currency: { type: "text", value: (i) => i.currency },
      symbol: { type: "text", value: (i) => i.market_symbol },
    },
    search: (i) => [i.instrument_id, i.name, typeLabel(i.asset_type), i.currency, i.market_symbol].filter(Boolean).join(" "),
    defaultSort: { key: "id", dir: "asc" },
    selects: [
      {
        key: "type",
        label: "Asset type",
        options: [{ value: "all", label: "All types" }, ...ASSET_TYPES.map((t) => ({ value: t, label: assetTypeOption(t) }))],
        test: (i, v) => v === "all" || i.asset_type === v,
      },
    ],
  });
  const columns: Column<Instrument>[] = [
    {
      key: "id",
      header: "ID",
      sortKey: "id",
      cell: (i) => (
        <Link className="id-chip" to={`/holding/${encodeURIComponent(i.instrument_id)}`} aria-label={`${i.instrument_id}: open details`}>
          {i.instrument_id}
        </Link>
      ),
    },
    {
      key: "name",
      header: "Name",
      sortKey: "name",
      className: "c-name",
      cell: (i) => (
        <Link className="name-link" to={`/holding/${encodeURIComponent(i.instrument_id)}`}>
          {i.name}
        </Link>
      ),
    },
    { key: "type", header: "Type", sortKey: "type", cell: (i) => typeLabel(i.asset_type) },
    { key: "quantity", header: "Quantity", sortKey: "quantity", align: "right", cell: (i) => (pending ? "…" : qty(qtyOf(i))) },
    {
      key: "avg_cost",
      header: "Avg cost",
      sortKey: "avg_cost",
      align: "right",
      className: "c-cost",
      cell: (i) => {
        if (pending) return "…";
        const a = avgOf(i);
        return isNum(a) ? price(a) : "—";
      },
    },
    { key: "currency", header: "Ccy", label: "Currency", sortKey: "currency", cell: (i) => i.currency },
    { key: "symbol", header: "Symbol", sortKey: "symbol", cell: (i) => i.market_symbol || "—" },
    {
      key: "actions",
      header: "Actions",
      hideHeader: true,
      className: "acts",
      cell: (i) => (
        <span className="acts-in">
          <button type="button" className="icon-btn" onClick={() => open({ edit: i })} aria-label={`Edit ${i.name}`} title={`Edit ${i.name}`}>
            <Pencil aria-hidden="true" />
          </button>
          {delState?.id !== i.instrument_id && (
            <button
              type="button"
              className="icon-btn"
              onClick={() => {
                setDelError(null);
                setDelState({ id: i.instrument_id, step: "ask" });
              }}
              aria-label={`Delete ${i.name}`}
              title={`Delete ${i.name}`}
            >
              <Trash2 aria-hidden="true" />
            </button>
          )}
        </span>
      ),
    },
  ];
  return (
    <>
      <div className="stack">
        {editing?.mode === "add" && (
          <AddHoldingWizard
            existing={list.data ?? null}
            onClose={() => {
              setEditing(null);
              window.requestAnimationFrame(() => addButton.current?.focus());
            }}
            onSaved={list.reload}
          />
        )}
        {editing?.mode === "edit" && (
          <HoldingForm
            formRef={formRef}
            mode={editing.mode}
            form={form}
            setForm={setForm}
            onCancel={() => setEditing(null)}
            onSaved={() => {
              setEditing(null);
              list.reload();
            }}
          />
        )}

        <Card as="section" aria-labelledby="hold-h">
          <CardHeader
            id="hold-h"
            title="Your holdings"
            aside={
              <button type="button" className="btn small add-btn" ref={addButton} onClick={() => open("add")}>
                <Plus aria-hidden="true" />
                {/* Same trick as the Ask button: both labels in one cell, the name stays "Add holding". */}
                <span className="swap-labels">
                  <span className="swap-rest">Add holding</span>
                  <span className="swap-hover" aria-hidden="true">
                    Track another
                  </span>
                </span>
              </button>
            }
          />
          {list.loading && !list.data ? (
            <BlockSkeleton rows={3} />
          ) : list.error && !list.data ? (
            <Alert tone="err">{list.error}</Alert>
          ) : items.length === 0 ? (
            <div className="empty">
              <b>No holdings yet</b>
              Add one by hand, or import many at once from a CSV.
              <div className="btn-row">
                <button type="button" className="btn" onClick={() => open("add")}>
                  Add a holding
                </button>
                <Link className="btn ghost" to="/import">
                  Import CSV
                </Link>
              </div>
            </div>
          ) : (
            <>
              {delError && (
                <div style={{ marginBottom: 10 }}>
                  <Alert tone="err">{delError}</Alert>
                </div>
              )}
              <DataTable
                sf={sf}
                columns={columns}
                rowKey={(i) => i.instrument_id}
                label="holdings"
                variant="stack"
                className="dt-hold"
                phoneSort={PHONE_SORT}
                expanded={(i) =>
                  delState?.id === i.instrument_id ? (
                    <div className="confirm" role="group" aria-label={`Confirm delete ${i.instrument_id}`}>
                      {delState.step === "ask" ? (
                        <>
                          <span>Delete {i.name}?</span>
                          <button type="button" className="btn danger small" disabled={delBusy} onClick={() => remove(i.instrument_id, 0)}>
                            Yes, delete
                          </button>
                        </>
                      ) : (
                        <>
                          <span>{delState.message}</span>
                          <button type="button" className="btn danger small" disabled={delBusy} onClick={() => remove(i.instrument_id, delState.count)}>
                            Delete anyway
                          </button>
                        </>
                      )}
                      <button type="button" className="btn ghost small" disabled={delBusy} onClick={() => setDelState(null)} autoFocus>
                        Cancel
                      </button>
                    </div>
                  ) : null
                }
              />
            </>
          )}
        </Card>
      </div>
    </>
  );
}

interface HoldingFormProps {
  formRef: RefObject<HTMLFormElement | null>;
  mode: "add" | "edit";
  form: FormState;
  setForm: (f: FormState) => void;
  onCancel: () => void;
  onSaved: () => void;
}

function HoldingForm({ formRef, mode, form, setForm, onCancel, onSaved }: HoldingFormProps) {
  const uid = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof FormState) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const fid = (k: string) => `${uid}${k}`;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const id = form.instrument_id.trim();
    const q = parseDecimal(form.quantity);
    const avg = parseDecimal(form.avg_cost);
    const div = parseDecimal(form.price_divisor);
    const currency = form.currency.trim().toUpperCase();
    if (!id) return setError("Enter an instrument ID.");
    if (!form.name.trim()) return setError("Enter a name.");
    if (q === null || q < 0) return setError("Quantity must be a number, zero or more.");
    if (form.avg_cost.trim() !== "" && (avg === null || avg < 0)) return setError("Average cost must be a number, or leave it empty.");
    if (!/^[A-Z]{3}$/.test(currency)) return setError("Currency is a 3-letter code such as HUF, EUR or USD.");
    if (div === null || div <= 0) return setError("Price divisor must be a number above zero (1 if unsure).");
    setBusy(true);
    try {
      await endpoints.saveInstrument({
        instrument_id: id,
        name: form.name.trim(),
        asset_type: form.asset_type,
        quantity: q,
        avg_cost: avg,
        currency,
        market_symbol: form.market_symbol.trim() || null,
        price_divisor: div,
        notes: form.notes.trim() || null,
      });
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card as="section" aria-labelledby={fid("h")}>
      <CardHeader id={fid("h")} title={mode === "add" ? "Add a holding" : `Edit ${form.instrument_id}`} />
      <form ref={formRef} className="form" onSubmit={submit} noValidate>
        <div className="form-grid">
          <div className="field">
            <label htmlFor={fid("id")}>Instrument ID</label>
            <input id={fid("id")} value={form.instrument_id} onChange={set("instrument_id")} disabled={mode === "edit"} autoComplete="off" spellCheck={false} required aria-describedby={fid("idh")} />
            <span id={fid("idh")} className="help">
              {mode === "edit" ? "The ID cannot change." : "Your own short key, for example VWCE or OTP-B."}
            </span>
          </div>
          <div className="field">
            <label htmlFor={fid("name")}>Name</label>
            <input id={fid("name")} value={form.name} onChange={set("name")} autoComplete="off" required />
          </div>
          <div className="field">
            <label htmlFor={fid("type")}>Asset type</label>
            <Select
              id={fid("type")}
              value={form.asset_type}
              onValueChange={(v) => set("asset_type")({ target: { value: v } })}
              options={ASSET_TYPES.map((t) => ({ value: t, label: assetTypeOption(t) }))}
            />
          </div>
          <div className="field">
            <label htmlFor={fid("ccy")}>Currency</label>
            <input id={fid("ccy")} value={form.currency} onChange={set("currency")} maxLength={3} autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="HUF" required />
          </div>
          <div className="field">
            <label htmlFor={fid("qty")}>Quantity</label>
            <input id={fid("qty")} inputMode="decimal" value={form.quantity} onChange={set("quantity")} autoComplete="off" required />
          </div>
          <div className="field">
            <label htmlFor={fid("avg")}>Average cost per unit, optional</label>
            <input id={fid("avg")} inputMode="decimal" value={form.avg_cost} onChange={set("avg_cost")} autoComplete="off" />
          </div>
          <div className="field wide">
            <label htmlFor={fid("sym")}>Market symbol, optional</label>
            <input id={fid("sym")} value={form.market_symbol} onChange={set("market_symbol")} autoComplete="off" spellCheck={false} aria-describedby={fid("symh")} />
            <span id={fid("symh")} className="help">
              Yahoo ticker (e.g. VWCE.DE, GC=F) or fund:&lt;ISIN&gt; for Hungarian funds
            </span>
          </div>
          <div className="field">
            <label htmlFor={fid("div")}>Price divisor</label>
            <input id={fid("div")} inputMode="decimal" value={form.price_divisor} onChange={set("price_divisor")} autoComplete="off" aria-describedby={fid("divh")} />
            <span id={fid("divh")} className="help">
              1 unless prices are quoted per 100 units or in minor units
            </span>
          </div>
          <div className="field">
            <label htmlFor={fid("notes")}>Notes, optional</label>
            <input id={fid("notes")} value={form.notes} onChange={set("notes")} autoComplete="off" />
          </div>
        </div>
        {error && <Alert tone="err">{error}</Alert>}
        <div className="btn-row">
          <button type="submit" className="btn" disabled={busy}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Saving…" : mode === "add" ? "Add holding" : "Save changes"}
          </button>
          <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </Card>
  );
}
