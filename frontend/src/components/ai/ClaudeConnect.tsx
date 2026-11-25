import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ExternalLink } from "lucide-react";
import { endpoints, errorMessage, type ClaudeStatus } from "../../api";
import { useResource } from "../../hooks";
import { Alert } from "../Alert";
import { Skeleton } from "../Skeleton";

const megabytes = (bytes: number) => Math.round(bytes / 1_000_000).toString();

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

  // Connect was clicked while Claude Code still had to be downloaded: continue to sign-in once it is installed.
  const [waiting, setWaiting] = useState(false);
  const waitingRef = useRef(false);
  waitingRef.current = waiting;

  // Installed after a click on Connect: get the sign-in link once the tab is visible.
  const [pendingSignIn, setPendingSignIn] = useState(false);

  // One live region that stays mounted, so screen readers announce each step of the connect flow.
  const [announce, setAnnounce] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const signInRef = useRef<HTMLAnchorElement>(null);
  const connectBtnRef = useRef<HTMLButtonElement>(null);

  const s = current ?? status.data;
  const download = s?.installed === false ? s.download : undefined;
  const downloading = waiting || !!download?.downloading;
  // Numbers from an earlier, finished attempt would flash on a retry: only a running download has any.
  const live = download?.downloading ? download : undefined;

  /** Move focus within this card, but never away from something the user is working on elsewhere. */
  const focusHere = (el: HTMLElement | null) => {
    const active = document.activeElement;
    if (el && (!active || active === document.body || rootRef.current?.contains(active))) el.focus();
  };

  const connect = async () => {
    setBusy("login");
    setError(null);
    setSuccess(null);
    try {
      const res = await endpoints.claudeLogin();
      if (res.url) {
        setUrl(res.url);
        setAnnounce("Sign-in link ready.");
      } else {
        setWaiting(true);
        setAnnounce("Downloading Claude Code. The sign-in link appears when it is done.");
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };
  const connectRef = useRef(connect);
  connectRef.current = connect;

  // What replaced the focused Connect button takes the focus: the progress, the sign-in link,
  // or the button again when a download ends without a link.
  useEffect(() => {
    if (waiting) focusHere(progressRef.current);
  }, [waiting]);
  useEffect(() => {
    if (url) focusHere(signInRef.current);
  }, [url]);
  const wasDownloading = useRef(false);
  useEffect(() => {
    if (wasDownloading.current && !downloading && !pendingSignIn) focusHere(connectBtnRef.current);
    wasDownloading.current = downloading;
  }, [downloading, pendingSignIn]);

  // A sign-in stays open 10 minutes: start it only once someone is looking, not while the tab is hidden.
  useEffect(() => {
    if (!pendingSignIn) return;
    const go = () => {
      if (document.visibilityState !== "visible") return;
      document.removeEventListener("visibilitychange", go);
      setPendingSignIn(false);
      void connectRef.current();
    };
    document.addEventListener("visibilitychange", go);
    go();
    return () => document.removeEventListener("visibilitychange", go);
  }, [pendingSignIn]);

  // The download runs on the server in the background; follow it here.
  useEffect(() => {
    if (!downloading) return;
    let stopped = false;
    let timer = 0;
    const tick = async () => {
      try {
        const fresh = await endpoints.claudeStatus();
        if (stopped) return;
        setCurrent((prev) => (prev?.loggedIn ? prev : fresh)); // never undo a sign-in that completed meanwhile
        if (fresh.installed || !fresh.download?.downloading) {
          const clicked = waitingRef.current;
          setWaiting(false);
          if (fresh.installed && clicked && !fresh.loggedIn) {
            setAnnounce("Claude Code is installed. Getting the sign-in link.");
            setPendingSignIn(true);
          } else if (!fresh.installed && !fresh.download?.error) {
            setError("The download stopped before it finished. Click Connect Claude to try again.");
          }
          return;
        }
      } catch {
        // A network blip is not a failed download: keep following it.
      }
      if (!stopped) timer = window.setTimeout(tick, 1500);
    };
    timer = window.setTimeout(tick, 1500);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [downloading]);

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
    body = (
      <Alert tone="warn">
        Claude Code is not installed on this server, so Claude cannot be connected here.
        {s.reason && <> {s.reason}</>}
      </Alert>
    );
  } else {
    const needsDownload = s.installed === false;
    const total = live?.total ?? null;
    const failed = downloading ? null : download?.error;
    body = (
      <>
        <p className="sub">Not connected. Connecting uses your Claude subscription; no API key is stored.</p>
        {needsDownload && s.install_note && url === null && !downloading && <p className="sub">{s.install_note}</p>}
        {failed && (
          <div style={{ marginTop: 12 }}>
            <Alert tone="err">{failed} Click Connect Claude to try again.</Alert>
          </div>
        )}
        {downloading ? (
          <div className="claude-dl" ref={progressRef} tabIndex={-1}>
            <p className="sub">Downloading Claude Code… {waiting ? "The sign-in link appears when it is done." : ""}</p>
            {total ? (
              <progress value={live?.done ?? 0} max={total} aria-label="Claude Code download" />
            ) : (
              <progress aria-label="Claude Code download" />
            )}
            <p className="sub" aria-hidden="true">
              {megabytes(live?.done ?? 0)} of {total ? megabytes(total) : "about 130"} MB
            </p>
          </div>
        ) : url === null ? (
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button type="button" className="btn" onClick={connect} disabled={busy !== null || pendingSignIn} ref={connectBtnRef}>
              {(busy === "login" || pendingSignIn) && <span className="spinner" aria-hidden="true" />}
              {busy === "login" || pendingSignIn ? "Connecting…" : "Connect Claude"}
            </button>
          </div>
        ) : (
          <ol className="steps">
            <li>
              <span>Sign in with your Claude subscription on the sign-in page.</span>
              <a className="btn ghost small" href={url} target="_blank" rel="noopener noreferrer" ref={signInRef}>
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
    <div className="claude-connect" ref={rootRef}>
      <h3 className="sub-h">Claude login</h3>
      <p className="sr-only" role="status">
        {announce}
      </p>
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
