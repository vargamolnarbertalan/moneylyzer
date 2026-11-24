import { getPref, onPrefsChange, usePref } from "./prefs";

/** The in-app animation switch (Settings → Appearance). "reduced" does what the operating
 *  system's reduced-motion setting does (styles.css, end of the motion section): WCAG 2.2.2 asks
 *  for a way inside the app to stop motion that repeats with live updates. */
export type Motion = "full" | "reduced";
const MOTIONS: readonly Motion[] = ["full", "reduced"];

function sync(): void {
  const reduced = getPref("motion") === "reduced";
  if (reduced) document.documentElement.dataset.motion = "reduced";
  else delete document.documentElement.dataset.motion;
}

/** Runs once at startup, and again whenever prefs arrive or change (also from another device). */
export function initMotion(): void {
  sync();
  onPrefsChange(sync);
}

/** True when animations driven from script (number counting, the donut sweep) should not run. */
export function reducedMotion(): boolean {
  if (document.documentElement.dataset.motion === "reduced") return true;
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function useMotion(): [Motion, (m: Motion) => void] {
  return usePref<Motion>("motion", MOTIONS, "full");
}
