import { useId, useState, type FormEvent, type ReactNode } from "react";
import { endpoints, errorMessage } from "../api";
import { useAuth } from "../auth";
import { useResource, useTitle } from "../hooks";
import { ACCEPT_LABEL, DISCLAIMER_PARAGRAPHS, DISCLAIMER_TITLE, DISCLAIMER_VERSION, LIABILITY_PARAGRAPH } from "../legal";
import { Alert } from "./Alert";
import { Skeleton } from "./Skeleton";

/** Versions are "YYYY-MM-DD" or "YYYY-MM-DD.n": newer sorts later (".n" compared as a number). */
function atLeast(version: string | null | undefined, required: string): boolean {
  if (!version) return false;
  const key = (v: string) => {
    const [date, n] = v.split(".");
    return [date ?? "", Number(n ?? 0)] as const;
  };
  const [a, an] = key(version);
  const [b, bn] = key(required);
  return a > b || (a === b && an >= bn);
}

/**
 * Shows the "not financial advice / no liability" notice once, right after setup or sign-in, and again
 * only when its wording (DISCLAIMER_VERSION) changes. The app stays behind it until the owner accepts;
 * the server records every acceptance with version and time (GET/POST /api/disclaimer).
 */
export function DisclaimerGate({ children }: { children: ReactNode }) {
  const state = useResource(() => endpoints.disclaimer(), []);
  const [acceptedNow, setAcceptedNow] = useState<string | null>(null);
  if (atLeast(acceptedNow ?? state.data?.accepted_version, DISCLAIMER_VERSION)) return <>{children}</>;
  if (!state.data) {
    return (
      <main className="auth">
        {state.error ? (
          <div className="card form">
            <Alert tone="err">Could not load the notice: {state.error}</Alert>
            <button type="button" className="btn" onClick={state.reload}>
              Try again
            </button>
          </div>
        ) : (
          <div role="status" aria-label="Loading">
            <Skeleton w={220} h={24} />
            <span className="sr-only">Loading…</span>
          </div>
        )}
      </main>
    );
  }
  return <DisclaimerScreen renewed={state.data.accepted_version !== null} onAccepted={setAcceptedNow} />;
}

function DisclaimerScreen({ renewed, onAccepted }: { renewed: boolean; onAccepted: (version: string) => void }) {
  useTitle(DISCLAIMER_TITLE);
  const uid = useId();
  const auth = useAuth();
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState<"accept" | "logout" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!checked) return;
    setBusy("accept");
    setError(null);
    try {
      const saved = await endpoints.acceptDisclaimer(DISCLAIMER_VERSION);
      onAccepted(saved.accepted_version ?? DISCLAIMER_VERSION); // the answer is enough: no second request
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  };

  const logOut = async () => {
    setBusy("logout");
    setError(null);
    try {
      await auth.signOut();
    } catch (err) {
      setError(`Not logged out: ${errorMessage(err)}`);
      setBusy(null);
    }
  };

  return (
    <main className="auth">
      <form className="card form legal" onSubmit={submit} aria-labelledby={`${uid}h`}>
        <img src="/logo.svg" alt="" width={56} height={56} />
        <h1 id={`${uid}h`}>{DISCLAIMER_TITLE}</h1>
        <p className="sub">
          {renewed ? "This notice has changed since you last accepted it. Please read it again." : "Please read this once before you start. It is shown again only if the wording changes."}
        </p>
        <div className="legal-text" role="region" aria-label="Notice text" tabIndex={0}>
          {DISCLAIMER_PARAGRAPHS.map((p) => (
            <p key={p}>{p}</p>
          ))}
          <p className="legal-liability">
            <strong>Liability. </strong>
            {LIABILITY_PARAGRAPH}
          </p>
        </div>
        <label className="check">
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          <span>{ACCEPT_LABEL}</span>
        </label>
        {error && <Alert tone="err">{error}</Alert>}
        <div className="btn-row">
          <button type="submit" className="btn" disabled={!checked || busy !== null}>
            {busy === "accept" && <span className="spinner" aria-hidden="true" />}
            {busy === "accept" ? "Saving…" : "Continue"}
          </button>
          <button type="button" className="btn ghost" disabled={busy !== null} onClick={() => void logOut()}>
            {busy === "logout" && <span className="spinner" aria-hidden="true" />}
            {busy === "logout" ? "Logging out…" : "Log out"}
          </button>
        </div>
      </form>
    </main>
  );
}
