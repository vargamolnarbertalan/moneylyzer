import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, endpoints, errorMessage, setUnauthorizedHandler } from "./api";
import { resetLive } from "./live";
import { setPrefsSync } from "./prefs";
import { navigate, normalizePath } from "./router";

export type AuthStatus =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "out"; setupRequired: boolean }
  | { kind: "in"; user: string };

interface AuthContextValue {
  status: AuthStatus;
  /** Called after a successful sign-in or first-run setup. */
  signedIn: (user: string) => void;
  signOut: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Where to go after signing in: the page that sent the user to /login. */
let returnTo: string | null = null;

/** Where to come back to after signing in: path plus query and hash (e.g. /analyst?holding=VWCE). */
export function rememberReturnTo(path: string): void {
  if (path === "/login") return;
  const target = path === "/advisor" ? "/analyst" : path; // the page's name until 2026-11
  returnTo = `${target}${window.location.search}${window.location.hash}`;
}

export function takeReturnTo(): string {
  const to = returnTo ?? "/";
  returnTo = null;
  return to;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  const check = useCallback(async () => {
    try {
      const s = await endpoints.authState();
      setStatus(s.user ? { kind: "in", user: s.user } : { kind: "out", setupRequired: s.setup_required });
    } catch (err) {
      setStatus({ kind: "error", message: errorMessage(err) });
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check, attempt]);

  // View preferences sync with the server only while signed in (pulls on sign-in and on tab focus).
  useEffect(() => {
    setPrefsSync(status.kind === "in");
  }, [status.kind]);

  // Any 401 from the API (session expired, signed out elsewhere) goes to the sign-in page.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      resetLive();
      rememberReturnTo(normalizePath(window.location.pathname));
      setStatus({ kind: "out", setupRequired: false });
      void check(); // refreshes setup_required, and notices if the session is in fact still valid
      navigate("/login", { replace: true });
    });
    return () => setUnauthorizedHandler(null);
  }, [check]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      signedIn: (user) => setStatus({ kind: "in", user }),
      // Throws when the server could not be reached: the HttpOnly session cookie can only be cleared by
      // the server, so pretending to be signed out would leave the session alive on a shared device.
      signOut: async () => {
        try {
          await endpoints.logout();
        } catch (err) {
          if (!(err instanceof ApiError && err.status === 401)) throw err; // 401: already signed out
        }
        resetLive();
        setStatus({ kind: "out", setupRequired: false });
        navigate("/login", { replace: true });
      },
      retry: () => {
        setStatus({ kind: "loading" });
        setAttempt((n) => n + 1);
      },
    }),
    [status],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
