import { useId, useMemo, useRef, useState, type FormEvent } from "react";
import { endpoints, errorMessage, type DataLevel, type Effort, type Preset, type Provider, type ProviderInput, type ProviderKind } from "../../api";
import { Alert } from "../Alert";
import { Link } from "../../router";
import { Select } from "../Select";

export const KIND_LABEL: Record<ProviderKind, string> = {
  claude_code: "Claude login",
  anthropic: "Claude API",
  openai_compat: "OpenAI-compatible",
};

const EFFORTS: readonly Effort[] = ["default", "low", "medium", "high", "xhigh", "max"];
const EFFORT_LABEL: Record<Effort, string> = {
  default: "Model default",
  low: "low",
  medium: "medium",
  high: "high",
  xhigh: "xhigh",
  max: "max",
};
const EFFORT_HELP =
  "How long Claude thinks before answering. Higher is slower and uses more of your plan or API budget. Model default sends no setting and leaves it to the model (usually medium on current Claude models).";
// Claude Code's own aliases (code.claude.com/docs/en/model-config, checked 2026-11-25); each follows the newest version.
const CLAUDE_ALIASES = ["opus", "sonnet", "haiku", "fable"];
const MODEL_HELP: Record<ProviderKind, string> = {
  claude_code:
    "A short name that always follows the newest version: opus (the default), sonnet (faster), haiku (fastest) or fable (the most capable and slowest; on some plans it uses usage credits instead of your plan's limits). A full model ID such as claude-opus-5-5 pins one version.",
  anthropic: "A full model ID, such as claude-opus-5-5, claude-sonnet-5-5 or claude-haiku-5-5. Short names like opus work only with Claude login.",
  openai_compat: "The model name exactly as this provider lists it, for example llama3.1:8b in Ollama.",
};
const CUSTOM: Preset = { id: "custom", label: "Custom OpenAI-compatible", kind: "openai_compat", base_url: "", models: [], local: false, needs_key: false };
const LOCAL_HINT =
  "From the Docker container your PC is host.docker.internal. Ollama listens on 127.0.0.1 by default; if the container can't reach it, start Ollama with OLLAMA_HOST=0.0.0.0. " +
  // measured: instructions 2-9k tokens (default to longest custom persona), news up to ~9k, plus holdings and history
  "Give the model a context window of 32,000 tokens or more (Ollama: OLLAMA_CONTEXT_LENGTH=32768; LM Studio: Context Length when loading the model; llama.cpp: -c 32768). A typical question with its instructions, portfolio and news is about 15,000 tokens, and a shorter window cuts off the start, safety rules included.";

/** The same rule the server uses to decide that a base URL stays on your network. */
export function isLocalUrl(url: string): boolean {
  let host = "";
  try {
    host = new URL(url).hostname.replace(/^\[|\]$/g, "");
  } catch {
    return false;
  }
  return (
    ["localhost", "127.0.0.1", "::1", "host.docker.internal"].includes(host) ||
    host.endsWith(".local") ||
    /^(10|192\.168|172\.(1[6-9]|2\d|3[01]))(\.\d{1,3}){2,3}$/.test(host)
  );
}

type FieldName = "model" | "base_url" | "effort" | "data_level" | "name";

function fieldOf(message: string): FieldName | null {
  if (/model id/i.test(message)) return "model";
  if (/base url|https|http/i.test(message)) return "base_url";
  if (/effort/i.test(message)) return "effort";
  if (/data_level/i.test(message)) return "data_level";
  return null;
}

interface ProviderFormProps {
  mode: "add" | "edit";
  provider?: Provider;
  presets: Preset[];
  onSaved: (id: string) => void;
  onCancel: () => void;
}

export function ProviderForm({ mode, provider, presets, onSaved, onCancel }: ProviderFormProps) {
  const uid = useId();
  const id = (k: string) => `${uid}${k}`;
  const builtin = provider?.builtin === true;
  const addPresets = useMemo(() => [...presets.filter((p) => p.kind !== "claude_code"), CUSTOM], [presets]);

  const [presetId, setPresetId] = useState<string>(addPresets[0]?.id ?? "custom");
  const preset = mode === "add" ? (addPresets.find((p) => p.id === presetId) ?? CUSTOM) : undefined;

  const [name, setName] = useState(provider?.name ?? addPresets[0]?.label ?? "");
  const [model, setModel] = useState(provider?.model ?? addPresets[0]?.models[0] ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? addPresets[0]?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [level, setLevel] = useState<DataLevel>(provider?.data_level ?? "relative");
  const [web, setWeb] = useState(provider?.web_research ?? false);
  const [effort, setEffort] = useState<Effort>(provider?.effort ?? "high");
  const [advanced, setAdvanced] = useState(Boolean(provider?.kind === "anthropic" && provider.base_url));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ field: FieldName | null; message: string } | null>(null);
  // The provider's own model list, loaded on request (a typed name is still allowed).
  const [listed, setListed] = useState<string[] | null>(null);
  const [listing, setListing] = useState(false);
  const [listNote, setListNote] = useState<{ ok: boolean; text: string } | null>(null);

  const kind: ProviderKind = provider ? provider.kind : (preset?.kind ?? "openai_compat");
  const effectiveUrl = kind === "openai_compat" || (kind === "anthropic" && advanced) ? baseUrl : "";
  const models = useMemo(() => {
    if (mode === "add") return preset?.models ?? [];
    const match = presets.find((p) => p.kind === kind && (kind === "openai_compat" ? p.base_url === provider?.base_url : true));
    return match?.models ?? [];
  }, [mode, preset, presets, kind, provider?.base_url]);
  const needsKey = mode === "add" ? (preset?.needs_key ?? false) : kind === "anthropic";
  const hint = provider?.key_hint;

  const choosePreset = (pid: string) => {
    const p = addPresets.find((x) => x.id === pid) ?? CUSTOM;
    setPresetId(pid);
    setName(p.label);
    setModel(p.models[0] ?? "");
    setBaseUrl(p.base_url);
    setAdvanced(false);
    setWeb(false);
    setEffort("high");
    setLevel("relative");
    setError(null);
    forgetList();
  };

  // Each request gets a number; an answer for an older one (the URL or provider changed meanwhile) is dropped.
  const listRequest = useRef(0);
  const forgetList = () => {
    listRequest.current += 1;
    setListed(null);
    setListNote(null);
    setListing(false);
  };
  const changeUrl = (value: string) => {
    setBaseUrl(value);
    forgetList();
  };

  const listModels = async () => {
    if (kind === "claude_code") return;
    const request = ++listRequest.current;
    setListing(true);
    setListNote(null);
    try {
      const res = await endpoints.listModels({
        kind,
        base_url: effectiveUrl.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        ...(provider && !clearKey ? { provider_id: provider.id } : {}),
      });
      if (request !== listRequest.current) return;
      setListed(res.models);
      const shown = res.models.length;
      setListNote(
        shown === 0
          ? { ok: false, text: "The provider listed no models." }
          : {
              ok: true,
              text:
                (res.total > shown ? `${res.total} models found; the first ${shown} are suggested.` : `${shown} models found.`) +
                " Pick one from the Model field's suggestions, or type a name.",
            },
      );
    } catch (err) {
      if (request === listRequest.current) setListNote({ ok: false, text: errorMessage(err) });
    } finally {
      if (request === listRequest.current) setListing(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!builtin && !name.trim()) return setError({ field: "name", message: "Enter a name." });
    if (!model.trim()) return setError({ field: "model", message: "Enter a model id." });
    if (kind === "openai_compat" && !baseUrl.trim()) return setError({ field: "base_url", message: "Enter the provider's base URL, e.g. https://api.openai.com/v1" });
    const body: ProviderInput = {
      ...(provider ? { id: provider.id } : {}),
      name: builtin ? (provider?.name ?? "Claude login") : name.trim(),
      kind,
      model: model.trim(),
      base_url: effectiveUrl.trim(),
      data_level: level,
      web_research: kind === "openai_compat" ? false : web,
      effort: kind === "openai_compat" ? (provider?.effort ?? "high") : effort,
      clear_key: clearKey,
    };
    if (apiKey.trim()) body.api_key = apiKey.trim();
    setBusy(true);
    try {
      const res = await endpoints.saveProvider(body);
      onSaved(res.id);
    } catch (err) {
      const message = errorMessage(err);
      setError({ field: fieldOf(message), message });
      setBusy(false);
    }
  };

  const fieldError = (f: FieldName) => (error?.field === f ? error.message : null);
  const errorText = (f: FieldName) => {
    const m = fieldError(f);
    return m ? (
      <span id={id(`${f}e`)} className="field-err" role="alert">
        {m}
      </span>
    ) : null;
  };
  const describe = (f: FieldName, help?: string) => [help, fieldError(f) ? id(`${f}e`) : null].filter(Boolean).join(" ") || undefined;
  const showLocalHint = (mode === "add" ? preset?.local === true : provider?.local === true) || (kind === "openai_compat" && isLocalUrl(baseUrl));
  const generalError = error && error.field === null ? error.message : null;

  return (
    <form className="form provider-form" onSubmit={submit} noValidate aria-label={mode === "add" ? "Add a model" : `Edit ${provider?.name}`}>
      {mode === "add" && (
        <div className="field">
          <label htmlFor={id("preset")}>Provider</label>
          <Select id={id("preset")} value={presetId} onValueChange={choosePreset} options={addPresets.map((p) => ({ value: p.id, label: p.label }))} />
          {showLocalHint && <span className="help">{LOCAL_HINT}</span>}
        </div>
      )}
      {mode === "edit" && showLocalHint && <p className="help">{LOCAL_HINT}</p>}

      <div className="form-grid">
        {!builtin && (
          <div className="field">
            <label htmlFor={id("name")}>Name</label>
            <input id={id("name")} value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" aria-describedby={describe("name")} aria-invalid={fieldError("name") ? true : undefined} />
            {errorText("name")}
          </div>
        )}
        <div className="field">
          <label htmlFor={id("model")}>Model</label>
          <input id={id("model")} list={id("models")} value={model} onChange={(e) => setModel(e.target.value)} autoComplete="off" spellCheck={false} aria-describedby={describe("model", id("modelh"))} aria-invalid={fieldError("model") ? true : undefined} />
          <datalist id={id("models")}>
            {(listed ?? (kind === "claude_code" ? CLAUDE_ALIASES : models)).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <span id={id("modelh")} className="help">
            {MODEL_HELP[kind]}
          </span>
          {kind !== "claude_code" && (
            <div className="btn-row">
              <button type="button" className="btn ghost small" onClick={listModels} disabled={listing || busy}>
                {listing && <span className="spinner" aria-hidden="true" />}
                {listing ? "Asking the provider…" : "List the provider's models"}
              </button>
            </div>
          )}
          <span className={listNote && !listNote.ok ? "field-err" : "help"} role="status">
            {listNote?.text}
          </span>
          {errorText("model")}
        </div>

        {kind === "openai_compat" && (
          <div className="field wide">
            <label htmlFor={id("url")}>Base URL</label>
            <input id={id("url")} value={baseUrl} onChange={(e) => changeUrl(e.target.value)} autoComplete="off" spellCheck={false} placeholder="https://api.example.com/v1" aria-describedby={describe("base_url")} aria-invalid={fieldError("base_url") ? true : undefined} />
            {errorText("base_url")}
          </div>
        )}
        {kind === "anthropic" && (
          <div className="field wide">
            <label className="check">
              <input type="checkbox" checked={advanced} onChange={(e) => { setAdvanced(e.target.checked); forgetList(); }} /> Advanced: use a custom base URL
            </label>
            {advanced && (
              <>
                <label className="sr-only" htmlFor={id("url")}>
                  Base URL
                </label>
                <input id={id("url")} value={baseUrl} onChange={(e) => changeUrl(e.target.value)} autoComplete="off" spellCheck={false} placeholder="https://…" aria-describedby={describe("base_url")} aria-invalid={fieldError("base_url") ? true : undefined} />
                {errorText("base_url")}
              </>
            )}
          </div>
        )}

        {kind !== "claude_code" && (
          <div className="field wide">
            <label htmlFor={id("key")}>API key{needsKey ? "" : " (optional)"}</label>
            <input id={id("key")} type="password" name="provider-api-key" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={hint ? "Leave empty to keep the saved key" : needsKey ? "Paste your API key" : ""} aria-describedby={hint ? id("keyh") : undefined} />
            {hint && (
              <span id={id("keyh")} className="help">
                {hint === "set" ? "A key is saved" : `Saved key ${hint}`} — leave empty to keep.
              </span>
            )}
            {hint && (
              <label className="check">
                <input type="checkbox" checked={clearKey} onChange={(e) => setClearKey(e.target.checked)} /> Remove the saved key
              </label>
            )}
          </div>
        )}

        {kind !== "openai_compat" && (
          <div className="field">
            <label htmlFor={id("effort")}>Effort</label>
            <Select
              id={id("effort")}
              value={effort}
              onValueChange={(v) => setEffort(v as Effort)}
              aria-describedby={describe("effort", id("efforth"))}
              options={EFFORTS.map((x) => ({ value: x, label: EFFORT_LABEL[x] }))}
            />
            <span id={id("efforth")} className="help">
              {EFFORT_HELP}
            </span>
            {errorText("effort")}
          </div>
        )}
        {kind !== "openai_compat" && (
          <div className="field">
            <span className="lbl">Web research</span>
            <label className="check">
              <input type="checkbox" checked={web} onChange={(e) => setWeb(e.target.checked)} /> Let the model search the web
            </label>
          </div>
        )}
      </div>

      <p className="note-inline">
        What this model may see (relative figures or full detail) is set on the{" "}
        <Link className="btn ghost small" to="/privacy#ai">
          Data &amp; privacy
        </Link>{" "}
        page. New models start with relative figures.
      </p>

      {generalError && <Alert tone="err">{generalError}</Alert>}

      <div className="btn-row">
        <button type="submit" className="btn" disabled={busy}>
          {busy && <span className="spinner" aria-hidden="true" />}
          {busy ? "Saving…" : mode === "add" ? "Add model" : "Save changes"}
        </button>
        <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}
