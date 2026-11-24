import { useCallback } from "react";
import { getPref, onPrefsChange, setPref, usePref } from "./prefs";

export type Theme = "dark" | "light";
const THEMES: readonly Theme[] = ["dark", "light"];
// public/theme-init.js applies the theme before first paint from this key, so it mirrors the synced pref.
const MIRROR = "lia-theme";

function readTheme(): Theme {
  return getPref("theme") === "light" ? "light" : "dark";
}

let applied: Theme | null = null;

/** Puts the current theme on <html> and in the pre-paint mirror; runs at startup and on every change. */
function sync(): void {
  const theme = readTheme();
  if (theme === applied) return;
  applied = theme;
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(MIRROR, theme);
  } catch {
    /* storage blocked: the next load starts dark until prefs arrive */
  }
}

/** Runs once at startup (theme-init.js already did it before first paint; this keeps React in step). */
export function initTheme(): void {
  sync();
  onPrefsChange(sync); // a theme chosen on another device arrives with the prefs sync
}

export function setTheme(theme: Theme): void {
  setPref("theme", theme);
}

export function useTheme(): [Theme, (t: Theme) => void, () => void] {
  const [theme, set] = usePref<Theme>("theme", THEMES, "dark");
  const toggle = useCallback(() => setTheme(readTheme() === "dark" ? "light" : "dark"), []);
  return [theme, set, toggle];
}
