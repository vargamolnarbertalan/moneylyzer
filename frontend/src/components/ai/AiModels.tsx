import { useEffect, useRef, useState } from "react";
import { endpoints, errorMessage, type Preset, type Provider } from "../../api";
import { useResource } from "../../hooks";
import { Alert } from "../Alert";
import { Card, CardHeader } from "../Card";
import { BlockSkeleton } from "../Skeleton";
import { ClaudeConnect } from "./ClaudeConnect";
import { KIND_LABEL, ProviderForm } from "./ProviderForm";

function kindLabel(p: Provider): string {
  return p.kind === "openai_compat" && p.local
    ? "Local · OpenAI-compatible"
    : KIND_LABEL[p.kind];
}

interface CardProps {
  p: Provider;
  presets: Preset[];
  onChanged: () => void;
  /** The list is reloading (after this card's or another card's change). */
  reloading: boolean;
}

function ProviderCard({ p, presets, onChanged, reloading }: CardProps) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"default" | "test" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<string | null>(null);

  const act = async (kind: "default" | "test" | "delete") => {
    setBusy(kind);
    setError(null);
    setTest(null);
    try {
      if (kind === "default") {
        await endpoints.makeDefaultProvider(p.id);
        onChanged();
      } else if (kind === "delete") {
        await endpoints.deleteProvider(p.id);
        onChanged();
      } else {
        const res = await endpoints.testProvider(p.id);
        setTest(`${res.model} replied: “${res.reply.trim() || "(empty)"}”`);
      }
      if (kind === "test") setBusy(null);
      // "default" and "delete" stay busy until the list reload they started has settled, whether
      // it worked or not (effect below), so the button cannot be pressed twice in between.
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  };
  const reloadStarted = useRef(false);
  useEffect(() => {
    if (reloading) reloadStarted.current = true;
    else if (reloadStarted.current) {
      reloadStarted.current = false;
      setBusy((b) => (b === "test" ? b : null));
    }
  }, [reloading]);

  return (
    <li className="provider">
      <span className="sr-only" role="status">
        {busy === "default"
          ? "Saving…"
          : busy === "delete"
            ? "Deleting…"
            : busy === "test"
              ? "Testing…"
              : ""}
      </span>
      <div className="provider-head">
        <div className="provider-title">
          <h3>{p.name}</h3>
          <div className="sub">
            <code>{p.model}</code> · {kindLabel(p)}
          </div>
        </div>
        <span className="badges">
          {p.is_default && <span className="badge accent">Default</span>}
          {p.local && <span className="badge">Local</span>}
          <span className={p.data_level === "full" ? "badge amber" : "badge"}>
            {p.data_level === "full" ? "Full detail" : "Relative data"}
          </span>
          {p.can_browse && <span className="badge">Web research</span>}
        </span>
      </div>

      {p.builtin && <ClaudeConnect />}

      <div className="btn-row provider-actions">
        {!p.is_default && (
          <button
            type="button"
            className="btn ghost small"
            onClick={() => act("default")}
            disabled={busy !== null}
          >
            {busy === "default" && (
              <span className="spinner" aria-hidden="true" />
            )}
            {busy === "default" ? "Saving…" : "Make default"}
          </button>
        )}
        <button
          type="button"
          className="btn ghost small"
          onClick={() => setEditing((e) => !e)}
          aria-expanded={editing}
        >
          {editing ? "Close editor" : "Edit"}
        </button>
        <button
          type="button"
          className="btn ghost small"
          onClick={() => act("test")}
          disabled={busy !== null}
        >
          {busy === "test" && <span className="spinner" aria-hidden="true" />}
          {busy === "test" ? "Testing…" : "Test"}
        </button>
        {!p.builtin && !confirming && (
          <button
            type="button"
            className="btn ghost small"
            onClick={() => setConfirming(true)}
            disabled={busy !== null}
          >
            Delete
          </button>
        )}
      </div>

      {confirming && (
        <div
          className="confirm"
          role="group"
          aria-label={`Confirm delete ${p.name}`}
        >
          <span>Delete {p.name}? Its saved API key is removed too.</span>
          <button
            type="button"
            className="btn danger small"
            onClick={() => act("delete")}
            disabled={busy !== null}
          >
            {busy === "delete" && (
              <span className="spinner" aria-hidden="true" />
            )}
            {busy === "delete" ? "Deleting…" : "Yes, delete"}
          </button>
          <button
            type="button"
            className="btn ghost small"
            onClick={() => setConfirming(false)}
            disabled={busy !== null}
            autoFocus
          >
            No
          </button>
        </div>
      )}

      <div aria-live="polite" className="provider-result">
        {test && <Alert tone="ok">{test}</Alert>}
        {error && <Alert tone="err">{error}</Alert>}
      </div>

      {editing && (
        <ProviderForm
          mode="edit"
          provider={p}
          presets={presets}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
        />
      )}
    </li>
  );
}

/** Settings -> AI models: the connected models as cards, plus the add-a-model form. */
export function AiModels() {
  const providers = useResource(() => endpoints.aiProviders(), []);
  const presets = useResource(() => endpoints.aiPresets(), []);
  const [adding, setAdding] = useState(false);

  return (
    <Card as="section" id="ai" className="ai-section" aria-labelledby="ai-h">
      <CardHeader
        id="ai-h"
        title="AI models"
        aside={
          !adding && (
            <button
              type="button"
              className="btn small"
              onClick={() => setAdding(true)}
              disabled={!presets.data}
            >
              Add a model
            </button>
          )
        }
      />
      <p className="sub">
        Connect any model: your Claude subscription, a provider's API, or one
        running on your own machine. The default one answers on the AI analyst
        page; you can switch per conversation.
      </p>

      {providers.loading && !providers.data ? (
        <BlockSkeleton rows={2} />
      ) : !providers.data ? (
        <Alert tone="err">
          {providers.error ?? "Could not load the models."}
        </Alert>
      ) : (
        <>
          {providers.error && !providers.loading && (
            <Alert tone="err">
              Could not refresh the list: {providers.error}{" "}
              <button
                type="button"
                className="link-btn"
                onClick={providers.reload}
              >
                Try again
              </button>
            </Alert>
          )}
          <ul className="plain-list providers">
            {providers.data.map((p) => (
              <ProviderCard
                key={p.id}
                p={p}
                presets={presets.data ?? []}
                onChanged={providers.reload}
                reloading={providers.loading}
              />
            ))}
          </ul>
        </>
      )}

      {adding && presets.data && (
        <div className="add-model">
          <h3 className="sub-h">Add a model</h3>
          <ProviderForm
            mode="add"
            presets={presets.data}
            onCancel={() => setAdding(false)}
            onSaved={() => {
              setAdding(false);
              providers.reload();
            }}
          />
        </div>
      )}
      {presets.error && !presets.data && (
        <Alert tone="err">
          Could not load the provider presets: {presets.error}
        </Alert>
      )}
    </Card>
  );
}
