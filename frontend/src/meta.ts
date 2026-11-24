// GET /api/meta (public): app version and the optional donation link. Fetched once per page load.
import { useEffect, useState } from "react";
import { api } from "./api";

export interface Meta {
  version: string;
  /** Only an http(s) URL is ever returned here; anything else is dropped. */
  donateUrl: string | null;
  /** The project's source repository (https only). */
  repository: string | null;
}

let cached: Promise<Meta | null> | null = null;

function load(): Promise<Meta | null> {
  cached ??= api
    .get<{ version?: unknown; donate_url?: unknown; repository?: unknown }>("/api/meta", { keepOn401: true })
    .then((m) => ({
      version: typeof m.version === "string" ? m.version : "",
      donateUrl: typeof m.donate_url === "string" && /^https?:\/\//i.test(m.donate_url) ? m.donate_url : null,
      repository: typeof m.repository === "string" && /^https:\/\//i.test(m.repository) ? m.repository : null,
    }))
    .catch(() => null);
  return cached;
}

export function useMeta(): Meta | null {
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => {
    let alive = true;
    void load().then((m) => {
      if (alive) setMeta(m);
    });
    return () => {
      alive = false;
    };
  }, []);
  return meta;
}
