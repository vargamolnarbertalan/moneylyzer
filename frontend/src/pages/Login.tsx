import { useState, type FormEvent } from "react";
import { SHORT_NOTICE } from "../legal";
import { endpoints, errorMessage } from "../api";
import { takeReturnTo, useAuth } from "../auth";
import { Alert } from "../components/Alert";
import { useTitle } from "../hooks";
import { navigate } from "../router";

export function LoginPage({ setupRequired }: { setupRequired: boolean }) {
  useTitle(setupRequired ? "Set up" : "Sign in");
  const auth = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (setupRequired) {
      if (password.length < 10) return setError("Use at least 10 characters for the password.");
      if (password !== repeat) return setError("The two passwords do not match.");
    }
    setBusy(true);
    try {
      const res = setupRequired
        ? await endpoints.setup(username.trim(), password, code.trim())
        : await endpoints.login(username.trim(), password);
      auth.signedIn(res.user);
      navigate(takeReturnTo(), { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <form className="card form" onSubmit={submit} noValidate>
        <div className="auth-brand">
          <img src="/logo.svg" alt="" width={72} height={72} />
          <p className="auth-name">Moneylyzer</p>
          <p className="sub">Your portfolio, analyzed — privately.</p>
        </div>
        <div>
          <h1>{setupRequired ? "Create your account" : "Sign in"}</h1>
          {setupRequired && (
            <p className="sub" style={{ marginTop: 4 }}>
              First run: this dashboard has no account yet.
            </p>
          )}
        </div>

        <div className="field">
          <label htmlFor="username">Username</label>
          <input id="username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete={setupRequired ? "new-password" : "current-password"}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-describedby={setupRequired ? "pw-help" : undefined}
          />
          {setupRequired && (
            <span id="pw-help" className="help">
              At least 10 characters.
            </span>
          )}
        </div>
        {setupRequired && (
          <>
            <div className="field">
              <label htmlFor="repeat">Repeat password</label>
              <input id="repeat" name="repeat" type="password" autoComplete="new-password" required value={repeat} onChange={(e) => setRepeat(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="code">Setup code</label>
              <input id="code" name="code" autoComplete="off" autoCapitalize="characters" spellCheck={false} required value={code} onChange={(e) => setCode(e.target.value)} aria-describedby="code-help" />
              <span id="code-help" className="help">
                Printed in the container log: run <code style={{ whiteSpace: "nowrap" }}>docker logs moneylyzer</code>
              </span>
            </div>
          </>
        )}

        {error && <Alert tone="err">{error}</Alert>}

        <button type="submit" className="btn" disabled={busy}>
          {busy && <span className="spinner" aria-hidden="true" />}
          {busy ? (setupRequired ? "Creating…" : "Signing in…") : setupRequired ? "Create account" : "Sign in"}
        </button>
      </form>
      <p className="auth-legal">{SHORT_NOTICE} Provided “as is”, without warranty.</p>
    </main>
  );
}
