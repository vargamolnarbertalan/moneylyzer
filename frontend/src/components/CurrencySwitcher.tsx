import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { endpoints, errorMessage } from "../api";
import { COMMON_CURRENCIES } from "../currencies";
import { useResource } from "../hooks";
import { useBase } from "../live";
import { Select } from "./Select";

const OTHER = "__other__";

/** Pick the reporting currency. The server pushes a new snapshot over SSE a few seconds after the change. */
export function CurrencySwitcher({ showLabel = false }: { showLabel?: boolean }) {
  const uid = useId();
  const base = useBase();
  const settings = useResource(() => endpoints.settings(), []);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [otherOpen, setOtherOpen] = useState(false);
  const [other, setOther] = useState("");
  const otherInput = useRef<HTMLInputElement>(null);
  const pickedOther = useRef(false);

  const list = settings.data?.currencies?.length ? settings.data.currencies : COMMON_CURRENCIES;
  const options = list.includes(base) ? list : [base, ...list];

  // The change is done when a snapshot in the new currency arrives; give up waiting after a while.
  useEffect(() => {
    if (pending && base === pending) setPending(null);
  }, [base, pending]);
  useEffect(() => {
    if (!pending) return;
    const t = window.setTimeout(() => setPending(null), 20000);
    return () => window.clearTimeout(t);
  }, [pending]);

  const apply = async (code: string) => {
    const c = code.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(c)) {
      setError("Use a 3-letter currency code, for example CHF.");
      return;
    }
    if (c === base) {
      setOtherOpen(false);
      return;
    }
    setError(null);
    setPending(c);
    try {
      await endpoints.saveSettings(c);
      setOtherOpen(false);
      setOther("");
      settings.reload();
    } catch (err) {
      setPending(null);
      setError(errorMessage(err));
    }
  };

  const onSelect = (value: string) => {
    pickedOther.current = value === OTHER;
    if (value === OTHER) {
      setOtherOpen(true);
      return;
    }
    setOtherOpen(false);
    void apply(value);
  };

  const submitOther = (e: FormEvent) => {
    e.preventDefault();
    void apply(other);
  };

  return (
    <div className="ccy">
      <div className="ccy-row">
        <label className={showLabel ? "ccy-label" : "sr-only"} htmlFor={`${uid}s`}>
          Display currency
        </label>
        <Select
          id={`${uid}s`}
          className="ccy-select"
          value={pending ?? base}
          disabled={pending !== null}
          onValueChange={onSelect}
          noClosedTypeahead
          onCloseAutoFocus={(e) => {
            // After "Other…" the code input must get the focus: left on the trigger, typing "S" would
            // select SEK through the list's typeahead and switch the reporting currency at once.
            if (!pickedOther.current) return;
            pickedOther.current = false;
            e.preventDefault();
            window.requestAnimationFrame(() => otherInput.current?.focus());
          }}
          options={[
            ...options.map((c) => ({ value: c, label: c })),
            ...(pending && !options.includes(pending) ? [{ value: pending, label: pending }] : []),
            { value: OTHER, label: "Other…" },
          ]}
        />
        {pending && (
          <span className="ccy-busy" role="status">
            <span className="spinner" aria-hidden="true" /> Recalculating in {pending}…
          </span>
        )}
      </div>
      {otherOpen && !pending && (
        <form className="ccy-row" onSubmit={submitOther} noValidate>
          <label className="sr-only" htmlFor={`${uid}o`}>
            Currency code
          </label>
          <input
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setOtherOpen(false);
                setOther("");
              }
            }}
            id={`${uid}o`} ref={otherInput} className="input ccy-other" value={other} maxLength={3} placeholder="e.g. TRY" autoCapitalize="characters" autoComplete="off" spellCheck={false} onChange={(e) => setOther(e.target.value)} autoFocus />
          <button type="submit" className="btn small">
            Apply
          </button>
        </form>
      )}
      {error && (
        <p className="ccy-err" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
