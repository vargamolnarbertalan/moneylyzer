import { useId, useState, type FormEvent } from "react";
import { ExternalLink } from "lucide-react";
import { endpoints, errorMessage, type ClaudeStatus } from "../../api";
import { useResource } from "../../hooks";
import { Alert } from "../Alert";
import { Skeleton } from "../Skeleton";

/** The Claude subscription connect flow: status, connect (link + code), disconnect. */
export function ClaudeConnect() {
  const status = useResource(() => endpoints.claudeStatus(), []);
  const [current, setCurrent] = useState<ClaudeStatus | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"login" | "code" | "logout" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const uid = useId();

  const s = current ?? status.data;

  const connect = async () => {
    setBusy("login");
    setError(null);
    setSuccess(null);
    try {
      const res = await endpoints.claudeLogin();
      setUrl(res.url);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return setError("Paste the code first.");
    setBusy("code");
    setError(null);
    try {
      const res = await endpoints.claudeCode(code.trim());
      setCurrent(res);
      setUrl(null);
      setCode("");
      setSuccess("Claude is connected.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async () => {
    setBusy("logout");
    setError(null);
    setSuccess(null);
    try {
      setCurrent(await endpoints.claudeLogout());
      setSuccess("Claude is disconnected.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  let body;
  if (!s) {
    body = status.error ? <Alert tone="err">{status.error}</Alert> : <Skeleton h={44} />;
  } else if (s.loggedIn) {
    body = (
      <>
        <dl className="facts">
          <div>
            <dt>Status</dt>
            <dd>Connected</dd>
          </div>
          {s.email && (
            <div>
              <dt>Account</dt>
              <dd>{s.email}</dd>
            </div>
          )}
          {s.subscription && (
            <div>
              <dt>Subscription</dt>
              <dd>{s.subscription}</dd>
            </div>
          )}
          {s.authMethod && (
            <div>
              <dt>Sign-in method</dt>
              <dd>{s.authMethod}</dd>
            </div>
          )}
        </dl>
        <div className="btn-row" style={{ marginTop: 12 }}>
          <button type="button" className="btn ghost small" onClick={disconnect} disabled={busy !== null}>
            {busy === "logout" && <span className="spinner" aria-hidden="true" />}
            {busy === "logout" ? "Disconnecting…" : "Disconnect"}
          </button>
        </div>
      </>
    );
  } else if (!s.available) {
    body = <Alert tone="warn">Claude Code is not installed on this server, so Claude cannot be connected here.</Alert>;
  } else {
    body = (
      <>
        <p className="sub">Not connected. Connecting uses your Claude subscription; no API key is stored.</p>
        {url === null ? (
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button type="button" className="btn" onClick={connect} disabled={busy !== null}>
              {busy === "login" && <span className="spinner" aria-hidden="true" />}
              {busy === "login" ? "Connecting…" : "Connect Claude"}
            </button>
          </div>
        ) : (
          <ol className="steps">
            <li>
              <span>Sign in with your Claude subscription on the sign-in page.</span>
              <a className="btn ghost small" href={url} target="_blank" rel="noopener noreferrer">
                <ExternalLink aria-hidden="true" />
                Open the sign-in page
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </li>
            <li>
              <form className="form" onSubmit={submitCode} noValidate>
                <div className="field">
                  <label htmlFor={`${uid}c`}>Paste the code shown after sign-in</label>
                  <input id={`${uid}c`} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" spellCheck={false} />
                </div>
                <div className="btn-row">
                  <button type="submit" className="btn" disabled={busy !== null}>
                    {busy === "code" && <span className="spinner" aria-hidden="true" />}
                    {busy === "code" ? "Checking…" : "Submit"}
                  </button>
                  <button type="button" className="btn ghost" onClick={() => setUrl(null)} disabled={busy !== null}>
                    Start over
                  </button>
                </div>
              </form>
            </li>
          </ol>
        )}
      </>
    );
  }

  return (
    <div className="claude-connect">
      <h3 className="sub-h">Claude login</h3>
      {body}
      {(error || success) && (
        <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
          {error && <Alert tone="err">{error}</Alert>}
          {success && <Alert tone="ok">{success}</Alert>}
        </div>
      )}
    </div>
  );
}
