import { Fragment, useId, useRef, useState, type FormEvent } from "react";
import { Download } from "lucide-react";
import { api, errorMessage } from "../api";
import { Alert } from "../components/Alert";
import { Card, CardHeader } from "../components/Card";
import { useTitle } from "../hooks";

const MAX_CSV_BYTES = 5 * 1024 * 1024;

interface ImportCardProps<R> {
  /** Its place in the import order (holdings first: the others refer to them). */
  step: number;
  title: string;
  blurb: string;
  endpoint: string;
  template: string;
  header: string;
  describe: (result: R) => string;
}

function ImportCard<R>({ step, title, blurb, endpoint, template, header, describe }: ImportCardProps<R>) {
  const uid = useId();
  const input = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return setError("Choose a CSV file first.");
    // the server refuses bigger bodies with a bare 413 before it reads the file (server/main.py MAX_UPLOAD_BYTES)
    if (file.size > MAX_CSV_BYTES) return setError("File is larger than 5 MB.");
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.upload<R>(endpoint, file);
      setResult(describe(res));
      setFile(null);
      if (input.current) input.current.value = "";
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card as="section" aria-labelledby={`${uid}h`}>
      <CardHeader
        id={`${uid}h`}
        title={
          <>
            <span className="step-no">
              <span className="sr-only">Step </span>
              {step}
            </span>
            {title}
          </>
        }
      />
      <p className="sub">{blurb}</p>
      <p className="sub" style={{ marginTop: 10 }}>
        First line of the file:
      </p>
      <p style={{ marginTop: 4 }}>
        {/* Breaks only after a comma, never inside a column name. */}
        <code className="csv-head">
          {header.split(",").map((col, i) => (
            <Fragment key={col}>
              {i > 0 && (
                <>
                  ,<wbr />
                </>
              )}
              {col}
            </Fragment>
          ))}
        </code>
      </p>
      <div className="btn-row" style={{ marginTop: 10 }}>
        <a className="btn ghost small" href={template} download>
          <Download aria-hidden="true" />
          Download template
        </a>
      </div>
      <form className="form" onSubmit={submit} noValidate style={{ marginTop: 14 }}>
        <div className="field">
          <label htmlFor={`${uid}f`}>CSV file (UTF-8, up to 5 MB)</label>
          <input id={`${uid}f`} ref={input} type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="btn-row">
          <button type="submit" className="btn" disabled={busy || !file}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Importing…" : "Import"}
          </button>
        </div>
        {result && <Alert tone="ok">{result}</Alert>}
        {error && <Alert tone="err">{error}</Alert>}
      </form>
    </Card>
  );
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function ImportPage() {
  useTitle("Import");
  return (
    <div className="stack">
      <p className="sub" style={{ maxWidth: "70ch" }}>
        Import in this order: holdings first, then purchases and price history, which refer to holdings by <code>instrument_id</code>.
      </p>
      <div className="cards-auto">
        <ImportCard<{ imported: number }>
          step={1}
          title="Holdings CSV"
          blurb="Adds new holdings and updates existing ones with the same instrument_id."
          endpoint="/api/import/holdings"
          template="/api/templates/holdings.csv"
          header="instrument_id,name,asset_type,quantity,avg_cost,currency,market_symbol,price_divisor,notes"
          describe={(r) => `Imported ${plural(r.imported, "holding", "holdings")}.`}
        />
        <ImportCard<{ added: number; skipped_duplicates: number }>
          step={2}
          title="Purchases CSV"
          blurb="One row per buy: date, units and price in the holding's currency, optionally what you paid in total (fees included) and the currency you paid in."
          endpoint="/api/import/lots"
          template="/api/templates/lots.csv"
          header="instrument_id,buy_date,quantity,unit_price,paid_amount,paid_currency,platform,notes"
          describe={(r) =>
            `Added ${plural(r.added, "purchase", "purchases")}` +
            (r.skipped_duplicates > 0 ? `, skipped ${plural(r.skipped_duplicates, "duplicate", "duplicates")}.` : ".")
          }
        />
        <ImportCard<{ imported: number }>
          step={3}
          title="Price history CSV"
          blurb="Daily closing prices for holdings that cannot be fetched automatically."
          endpoint="/api/import/prices"
          template="/api/templates/prices.csv"
          header="instrument_id,date,close,currency"
          describe={(r) => `Imported ${plural(r.imported, "price", "prices")}.`}
        />
      </div>
    </div>
  );
}
