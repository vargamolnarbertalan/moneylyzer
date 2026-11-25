import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { CodeXml, FileUp, Scale, ShieldCheck } from "lucide-react";
import { endpoints, errorMessage } from "../api";
import { useAuth } from "../auth";
import { Alert } from "../components/Alert";
import { AdvisorRules } from "../components/ai/AdvisorRules";
import { AiModels } from "../components/ai/AiModels";
import { PersonaSettings } from "../components/ai/PersonaSettings";
import { Card, CardHeader } from "../components/Card";
import { CoffeeButton } from "../components/Coffee";
import { CurrencySwitcher } from "../components/CurrencySwitcher";
import { Segmented } from "../components/Segmented";
import { useResource, useTitle } from "../hooks";
import { DISCLAIMER_PARAGRAPHS, DISCLAIMER_TITLE, LIABILITY_PARAGRAPH } from "../legal";
import { useBase } from "../live";
import { useMeta } from "../meta";
import { Link, useHash } from "../router";
import { useTheme, type Theme } from "../theme";
import { useMotion, type Motion } from "../motion";

const MOTIONS = [
  { value: "full", label: "On" },
  { value: "reduced", label: "Reduced" },
] as const;

const THEMES = [
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
] as const;

const LIVE_LABELS: Record<number, { label: string; name: string }> = {
  15: { label: "15 s", name: "15 seconds" },
  30: { label: "30 s", name: "30 seconds" },
  60: { label: "1 min", name: "1 minute" },
  120: { label: "2 min", name: "2 minutes" },
  300: { label: "5 min", name: "5 minutes" },
  900: { label: "15 min", name: "15 minutes" },
};

/** How often live prices refresh; saved on the server and applied at once (no restart). */
function LiveInterval() {
  const settings = useResource(() => endpoints.settings(), []);
  const [chosen, setChosen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0); // only the newest click's answer may set what is shown
  const data = settings.data;
  if (!data) return settings.error ? <Alert tone="err">Could not load the refresh interval: {settings.error}</Alert> : null;
  const value = String(chosen ?? data.live_seconds);
  const choose = async (v: string) => {
    const request = ++latest.current;
    setChosen(Number(v));
    setError(null);
    try {
      const saved = await endpoints.saveLiveSeconds(Number(v));
      if (request === latest.current) setChosen(saved.live_seconds); // what the server now uses
    } catch (err) {
      if (request !== latest.current) return;
      setError(errorMessage(err));
      setChosen(null);
      settings.reload(); // show the interval the server really has
    }
  };
  return (
    <>
      <p className="sub" style={{ margin: "14px 0 10px" }}>
        Live prices and exchange rates refresh this often while Moneylyzer is open in a browser. With no tab open (or
        one in the background for a minute), live prices pause until you come back; daily prices and news are still
        collected hourly.
      </p>
      <Segmented
        label="Live price refresh"
        options={data.live_choices.map((s) => ({ value: String(s), label: LIVE_LABELS[s]?.label ?? `${s} s`, name: LIVE_LABELS[s]?.name }))}
        value={value}
        onChange={(v) => void choose(v)}
      />
      {error && (
        <p className="field-err" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

export function SettingsPage() {
  useTitle("Settings");
  const [theme, setTheme] = useTheme();
  const [motion, setMotion] = useMotion();
  const auth = useAuth();
  const meta = useMeta();
  const base = useBase();
  const hash = useHash();
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  // "/settings#ai" lands on the section it names.
  useEffect(() => {
    if (!hash) return;
    const t = window.setTimeout(() => document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView({ block: "start" }), 60);
    return () => window.clearTimeout(t);
  }, [hash]);

  return (
    <div className="stack">
      <div className="cols2">
        <div className="stack">
          <Card as="section" aria-labelledby="theme-h">
            <CardHeader id="theme-h" title="Appearance" />
            <p className="sub" style={{ marginBottom: 10 }}>
              Dark is the default; your choice is saved to your account and follows you to your other devices.
            </p>
            <Segmented<Theme> label="Theme" options={THEMES} value={theme} onChange={setTheme} />
            <p className="sub" style={{ margin: "14px 0 10px" }}>
              Animations: charts drawing in, numbers counting on live updates, the pulsing live dot. Reduced stops all of
              them (your device's reduced-motion setting does the same).
            </p>
            <Segmented<Motion> label="Animations" options={MOTIONS} value={motion} onChange={setMotion} />
          </Card>

          <Card as="section" aria-labelledby="display-h">
            <CardHeader id="display-h" title="Display" />
            <p className="sub" style={{ marginBottom: 10 }}>
              Totals, P/L and charts are shown in {base}. Rates come from Yahoo Finance; each past day uses that day's rate.
            </p>
            <CurrencySwitcher showLabel />
            <LiveInterval />
          </Card>
        </div>

        <div className="stack">
          <Card as="section" aria-labelledby="data-h">
            <CardHeader id="data-h" title="Your data" />
            <p className="sub">The database backup is one SQLite file with your holdings, transactions, stored prices and settings.</p>
            <div className="btn-row" style={{ marginTop: 12 }}>
              <a className="btn ghost" href="/api/backup">
                Download database backup
              </a>
              <button
                type="button"
                className="btn ghost"
                disabled={signingOut}
                onClick={async () => {
                  setSigningOut(true);
                  setSignOutError(null);
                  try {
                    await auth.signOut();
                  } catch (err) {
                    setSignOutError(`Not logged out: ${errorMessage(err)}. You are still signed in; try again.`);
                    setSigningOut(false);
                  }
                }}
              >
                {signingOut && <span className="spinner" aria-hidden="true" />}
                {signingOut ? "Logging out…" : "Log out"}
              </button>
            </div>
            {signOutError && <Alert tone="err">{signOutError}</Alert>}
          </Card>

          <Card as="section" aria-labelledby="about-h">
            <CardHeader id="about-h" title="About" />
            <p className="sub">
              Moneylyzer{meta?.version ? ` v${meta.version}` : ""} is free and open source (MIT). Supporting it is optional.
            </p>
            <div className="btn-row" style={{ marginTop: 12 }}>
              {meta?.donateUrl && <CoffeeButton url={meta.donateUrl} />}
              {meta?.repository && (
                <a className="btn ghost small" href={meta.repository} target="_blank" rel="noopener noreferrer">
                  <CodeXml aria-hidden="true" />
                  Source code on GitHub
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              )}
              <a className="btn ghost small" href="/third-party-licenses.txt" target="_blank" rel="noopener noreferrer">
                <Scale aria-hidden="true" />
                Third-party licences
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </div>
            <LegalNotice />
          </Card>
        </div>
      </div>

      <AiModels />
      <AdvisorRules />
      <PersonaSettings />

      <div className="cols2">
        <PasswordForm />

        <Card as="section" aria-labelledby="more-h">
          <CardHeader id="more-h" title="More" />
          <p className="sub">What leaves this server, and what each AI model may see, is on the Data &amp; privacy page.</p>
          <div className="btn-row" style={{ marginTop: 12 }}>
            <Link className="btn ghost small" to="/privacy">
              <ShieldCheck aria-hidden="true" />
              Data &amp; privacy
            </Link>
            <Link className="btn ghost small" to="/import">
              <FileUp aria-hidden="true" />
              Import CSV
            </Link>
          </div>
        </Card>
      </div>

    </div>
  );
}

/** The full "not financial advice" notice (src/legal.ts) and when the owner accepted it. */
function LegalNotice() {
  const state = useResource(() => endpoints.disclaimer(), []);
  const at = state.data?.accepted_at ? new Date(state.data.accepted_at) : null;
  return (
    <details className="legal-details" style={{ marginTop: 14 }}>
      <summary>
        {DISCLAIMER_TITLE}
        {at && !Number.isNaN(at.getTime()) ? ` · accepted ${at.toLocaleDateString()}` : ""}
      </summary>
      <div className="legal-text">
        {DISCLAIMER_PARAGRAPHS.map((t) => (
          <p key={t}>{t}</p>
        ))}
        <p className="legal-liability">
          <strong>Liability. </strong>
          {LIABILITY_PARAGRAPH}
        </p>
      </div>
    </details>
  );
}

function PasswordForm() {
  const authStatus = useAuth().status;
  const account = authStatus.kind === "in" ? authStatus.user : "";
  const uid = useId();
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (newPw.length < 10) return setError("Use at least 10 characters for the new password.");
    if (newPw !== repeat) return setError("The two new passwords do not match.");
    setBusy(true);
    try {
      await endpoints.changePassword(oldPw, newPw);
      setDone(true);
      setOldPw("");
      setNewPw("");
      setRepeat("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card as="section" aria-labelledby={`${uid}h`}>
      <CardHeader id={`${uid}h`} title="Change password" />
      <form className="form" onSubmit={submit} noValidate>
        {/* For password managers: which account this new password belongs to (not shown, not focusable). */}
        <input type="text" name="username" autoComplete="username" value={account} readOnly hidden aria-hidden="true" />
        <div className="field">
          <label htmlFor={`${uid}o`}>Current password</label>
          <input id={`${uid}o`} type="password" autoComplete="current-password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor={`${uid}n`}>New password</label>
          <input id={`${uid}n`} type="password" autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} required aria-describedby={`${uid}nh`} />
          <span id={`${uid}nh`} className="help">
            At least 10 characters. Other signed-in devices are signed out.
          </span>
        </div>
        <div className="field">
          <label htmlFor={`${uid}r`}>Repeat new password</label>
          <input id={`${uid}r`} type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} required />
        </div>
        {error && <Alert tone="err">{error}</Alert>}
        {done && <Alert tone="ok">Password changed.</Alert>}
        <div className="btn-row">
          <button type="submit" className="btn" disabled={busy}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Changing…" : "Change password"}
          </button>
        </div>
      </form>
    </Card>
  );
}
