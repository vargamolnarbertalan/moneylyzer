import { ChevronDown } from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";
import { endpoints, errorMessage, type Horizon, type Preferences, type Risk } from "../../api";
import { useResource } from "../../hooks";
import { Alert } from "../Alert";
import { Card, CardHeader } from "../Card";
import { BlockSkeleton } from "../Skeleton";
import { Select } from "../Select";

const MAX_PERSONA = 20000;
const MAX_NOTES = 2000;
const MAX_COUNTRY = 60; // analyzer/persona.py MAX_COUNTRY_CHARS

const HORIZONS: readonly { value: Horizon; label: string }[] = [
  { value: "short", label: "Short (under 2 years)" },
  { value: "medium", label: "Medium (2 to 7 years)" },
  { value: "long", label: "Long (7 years or more)" },
];
const RISKS: readonly { value: Risk; label: string }[] = [
  { value: "conservative", label: "Conservative" },
  { value: "moderate", label: "Moderate" },
  { value: "growth", label: "Growth" },
];
const LANGUAGES: readonly { value: Preferences["language"]; label: string }[] = [
  { value: "auto", label: "Automatic (match my question)" },
  { value: "en", label: "English" },
  { value: "hu", label: "Magyar" },
];

const count = new Intl.NumberFormat("hu-HU", { useGrouping: "always" });

/** Settings -> AI analyst persona: the editable ruleset (Markdown) and the user's investing preferences. */
export function PersonaSettings() {
  const uid = useId();
  const id = (k: string) => `${uid}${k}`;
  const res = useResource(() => endpoints.aiSettings(), []);
  const [ready, setReady] = useState(false);
  const [persona, setPersona] = useState("");
  const [defaultPersona, setDefaultPersona] = useState("");
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [personaBusy, setPersonaBusy] = useState(false);
  const [personaMsg, setPersonaMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [prefBusy, setPrefBusy] = useState(false);
  const [prefMsg, setPrefMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirmShort, setConfirmShort] = useState(false);
  const [understood, setUnderstood] = useState(false);

  useEffect(() => {
    if (res.data && !ready) {
      setPersona(res.data.persona);
      setDefaultPersona(res.data.default_persona);
      setPrefs(res.data.preferences);
      setReady(true);
    }
  }, [res.data, ready]);

  if (!ready || !prefs) {
    return res.error && !res.data ? (
      <Card as="section" id="persona" aria-labelledby="persona-h">
        <CardHeader id="persona-h" title="AI analyst persona" />
        <Alert tone="err">{res.error}</Alert>
      </Card>
    ) : (
      <BlockSkeleton rows={4} />
    );
  }

  const savePersona = async (e: FormEvent) => {
    e.preventDefault();
    setPersonaBusy(true);
    setPersonaMsg(null);
    try {
      const saved = await endpoints.saveAiSettings({ persona });
      setPersona(saved.persona);
      setDefaultPersona(saved.default_persona);
      setPersonaMsg({ ok: true, text: saved.is_default_persona ? "Saved. This is the default persona." : "Persona saved." });
    } catch (err) {
      setPersonaMsg({ ok: false, text: errorMessage(err) });
    } finally {
      setPersonaBusy(false);
    }
  };

  // Only the keys given are saved (the server merges), so a tab loaded earlier cannot undo the short-term
  // switch from another device: the switch sends only allow_short_term, the form never sends it.
  const savePrefs = async (next: Partial<Preferences>, okText = "Preferences saved.") => {
    setPrefBusy(true);
    setPrefMsg(null);
    try {
      const saved = await endpoints.saveAiSettings({ preferences: next });
      setPrefs(saved.preferences);
      setPrefMsg({ ok: true, text: okText });
      return true;
    } catch (err) {
      setPrefMsg({ ok: false, text: errorMessage(err) });
      return false;
    } finally {
      setPrefBusy(false);
    }
  };

  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs({ ...prefs, [k]: v });

  const onShortTerm = (checked: boolean) => {
    if (checked) {
      setUnderstood(false);
      setConfirmShort(true);
    } else {
      setConfirmShort(false);
      void savePrefs({ allow_short_term: false }, "Short-term help is off.");
    }
  };

  const enableShortTerm = async () => {
    if (!understood) return;
    if (await savePrefs({ allow_short_term: true }, "Short-term help is on.")) setConfirmShort(false);
  };

  return (
    <Card as="section" id="persona" aria-labelledby="persona-h">
      <CardHeader id="persona-h" title="AI analyst persona" />
      <p className="sub">
        Every model you connect gets this as its starting ruleset. Safety guardrails (no greed/fear-driven advice, no impulse or day trading unless enabled below) are always added on top and can't be removed here.
      </p>

      {/* The persona is long and rarely edited: folded, with its state on the summary. */}
      <details className="fold">
        <summary>
          <ChevronDown aria-hidden="true" />
          Edit the persona
          <span className="fold-state">{persona === defaultPersona ? "default" : "customised"}</span>
        </summary>
      <form className="form" onSubmit={savePersona} style={{ marginTop: 12 }}>
        <div className="field">
          <label htmlFor={id("persona")}>Persona (Markdown)</label>
          <textarea id={id("persona")} className="mono" rows={18} value={persona} maxLength={MAX_PERSONA} spellCheck={false} onChange={(e) => setPersona(e.target.value)} aria-describedby={id("count")} />
          <div className="field-foot">
            <span id={id("count")} className="help">
              {count.format(persona.length)} / {count.format(MAX_PERSONA)} characters
            </span>
          </div>
        </div>
        <div className="btn-row">
          <button type="submit" className="btn" disabled={personaBusy || !persona.trim()}>
            {personaBusy && <span className="spinner" aria-hidden="true" />}
            {personaBusy ? "Saving…" : "Save persona"}
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              setPersona(defaultPersona);
              setPersonaMsg(null);
            }}
            disabled={personaBusy || persona === defaultPersona}
          >
            Reset to default
          </button>
        </div>
        {personaMsg && <Alert tone={personaMsg.ok ? "ok" : "err"}>{personaMsg.text}</Alert>}
      </form>
      </details>

      <hr className="divider" />

      <h3 className="sub-h">Your investing preferences</h3>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          const { allow_short_term: _keep, ...rest } = prefs;
          void savePrefs(rest);
        }}
      >
        <div className="form-grid">
          <div className="field">
            <label htmlFor={id("horizon")}>Time horizon</label>
            <Select id={id("horizon")} value={prefs.horizon} onValueChange={(v) => set("horizon", v as Horizon)} options={HORIZONS} />
          </div>
          <div className="field">
            <label htmlFor={id("risk")}>Risk appetite</label>
            <Select id={id("risk")} value={prefs.risk} onValueChange={(v) => set("risk", v as Risk)} options={RISKS} />
          </div>
          <div className="field">
            <label htmlFor={id("lang")}>Answer language</label>
            <Select id={id("lang")} value={prefs.language} onValueChange={(v) => set("language", v as Preferences["language"])} options={LANGUAGES} />
          </div>
          <div className="field">
            <label htmlFor={id("country")}>Country you live in</label>
            <input id={id("country")} type="text" value={prefs.country} maxLength={MAX_COUNTRY} autoComplete="country-name" onChange={(e) => set("country", e.target.value)} placeholder="e.g. Hungary" aria-describedby={id("countryh")} />
            <span id={id("countryh")} className="help">So the analyst compares the banks, brokers and apps you can actually use. Empty: it works it out from your data and asks when unsure. Sent with every question.</span>
          </div>
          <div className="field wide">
            <label htmlFor={id("notes")}>Notes</label>
            <textarea id={id("notes")} rows={4} value={prefs.notes} maxLength={MAX_NOTES} onChange={(e) => set("notes", e.target.value)} placeholder="Goals and values — e.g. 'saving for a flat in 2030'. Hard constraints go in AI analyst rules." aria-describedby={id("notesh")} />
            <span id={id("notesh")} className="help">
              {count.format(prefs.notes.length)} / {count.format(MAX_NOTES)} characters. Shared with every model you ask.
            </span>
          </div>
        </div>

        <div className="field">
          <label className="check switch">
            <input type="checkbox" checked={prefs.allow_short_term || confirmShort} onChange={(e) => onShortTerm(e.target.checked)} disabled={prefBusy} aria-describedby={confirmShort ? id("short") : undefined} />
            <span>Help me with short-term / day trading</span>
          </label>
          <span className="help">Off by default: the AI analyst keeps to your horizon and won't encourage impulse trades.</span>
        </div>
        {confirmShort && !prefs.allow_short_term && (
          <div id={id("short")} className="warn-panel" role="group" aria-label="Confirm short-term trading help">
            <p>Most short-term traders lose money after costs. Enable only if you really want the AI analyst to help with it.</p>
            <label className="check">
              <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand and want short-term trading help
            </label>
            <div className="btn-row">
              <button type="button" className="btn small" onClick={enableShortTerm} disabled={!understood || prefBusy}>
                Enable
              </button>
              <button type="button" className="btn ghost small" onClick={() => setConfirmShort(false)} disabled={prefBusy}>
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="btn-row">
          <button type="submit" className="btn" disabled={prefBusy}>
            {prefBusy && <span className="spinner" aria-hidden="true" />}
            {prefBusy ? "Saving…" : "Save preferences"}
          </button>
        </div>
        {prefMsg && <Alert tone={prefMsg.ok ? "ok" : "err"}>{prefMsg.text}</Alert>}
      </form>
    </Card>
  );
}
