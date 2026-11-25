"""Model providers behind one call: `complete(provider, system, messages)`.

Three kinds:
- claude_code   — the Claude Code CLI on the user's Claude subscription (first-class, default).
                  Can research the web itself (WebSearch/WebFetch) when enabled; no file or shell tools.
- anthropic     — the Claude API with an API key, through the official `anthropic` SDK; optional
                  server-side web search; refusal fallback enabled.
- openai_compat — any OpenAI-compatible Chat Completions endpoint: OpenAI, Gemini, Mistral, Groq,
                  OpenRouter, DeepSeek, xAI, and local servers (Ollama, LM Studio, llama.cpp).
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from analyzer import secrets_store

log = logging.getLogger("lia")
from analyzer.ai import TIMEOUT_SECONDS, WORK_DIR, cli_environment, find_claude_cli, parse_cli_output

KINDS = ("claude_code", "anthropic", "openai_compat")
# "default" sends no effort: the model's own default (medium on current Claude models in Claude Code).
EFFORTS = ("default", "low", "medium", "high", "xhigh", "max")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\-\[\]~]{0,127}")
MAX_TOKENS = 16000

# Claude API models on which `fallbacks: "default"` and `output_config.effort` are documented.
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
EFFORT_MODELS = FALLBACK_MODELS | {"claude-haiku-5-5"}

# Verified 2026-11-24 against each vendor's docs (base URL + example models). Users can type any model id.
PRESETS = [
    {"id": "claude-login", "label": "Claude (your subscription login)", "kind": "claude_code", "base_url": "",
     "models": ["opus", "fable", "sonnet"], "local": False, "needs_key": False},
    {"id": "anthropic", "label": "Claude API (API key)", "kind": "anthropic", "base_url": "",
     "models": ["claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5"], "local": False, "needs_key": True},
    {"id": "openai", "label": "OpenAI", "kind": "openai_compat", "base_url": "https://api.openai.com/v1",
     "models": ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6-luna"], "local": False, "needs_key": True},
    {"id": "gemini", "label": "Google Gemini", "kind": "openai_compat",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "models": ["gemini-4-hydrogen", "gemini-3.8-flash"], "local": False, "needs_key": True},
    {"id": "mistral", "label": "Mistral", "kind": "openai_compat", "base_url": "https://api.mistral.ai/v1",
     "models": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"], "local": False, "needs_key": True},
    {"id": "groq", "label": "Groq", "kind": "openai_compat", "base_url": "https://api.groq.com/openai/v1",
     "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"], "local": False, "needs_key": True},
    {"id": "openrouter", "label": "OpenRouter (many models)", "kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1",
     "models": ["anthropic/claude-sonnet-5.5", "openai/gpt-6.2-sol", "google/gemini-4-hydrogen"], "local": False, "needs_key": True},
    {"id": "deepseek", "label": "DeepSeek", "kind": "openai_compat", "base_url": "https://api.deepseek.com",
     "models": ["deepseek-flash", "deepseek-v4-pro"], "local": False, "needs_key": True},
    {"id": "xai", "label": "xAI Grok", "kind": "openai_compat", "base_url": "https://api.x.ai/v1",
     "models": ["grok-4.7"], "local": False, "needs_key": True},
    {"id": "ollama", "label": "Ollama (local)", "kind": "openai_compat", "base_url": "http://host.docker.internal:11434/v1",
     "models": ["llama3.2", "gpt-oss:20b"], "local": True, "needs_key": False},
    {"id": "lmstudio", "label": "LM Studio (local)", "kind": "openai_compat", "base_url": "http://host.docker.internal:1234/v1",
     "models": [], "local": True, "needs_key": False},
    {"id": "llamacpp", "label": "llama.cpp server (local)", "kind": "openai_compat", "base_url": "http://host.docker.internal:8080/v1",
     "models": [], "local": True, "needs_key": False},
]


class LLMError(RuntimeError):
    pass


@dataclass
class Provider:
    id: str
    name: str
    kind: str
    model: str
    base_url: str = ""
    data_level: str = "relative"   # relative | full
    web_research: bool = False
    effort: str = "high"

    @property
    def secret_name(self) -> str:
        return f"provider:{self.id}"

    @property
    def is_local(self) -> bool:
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        return self.kind == "openai_compat" and (
            host in ("localhost", "127.0.0.1", "::1", "host.docker.internal") or host.endswith(".local")
            or re.fullmatch(r"(10|192\.168|172\.(1[6-9]|2\d|3[01]))(\.\d{1,3}){2,3}", host) is not None
        )

    @property
    def can_browse(self) -> bool:
        return self.web_research and self.kind in ("claude_code", "anthropic")


def validate(p: Provider) -> None:
    if p.kind not in KINDS:
        raise LLMError(f"Unknown provider type: {p.kind}")
    if not MODEL_RE.fullmatch(p.model or ""):
        raise LLMError("Enter a model id (letters, digits and . _ : / @ - only).")
    if p.data_level not in ("relative", "full"):
        raise LLMError("data_level must be relative or full")
    if p.effort not in EFFORTS:
        raise LLMError("effort must be default, low, medium, high, xhigh or max")
    check_base_url(p.kind, p.base_url)


def check_base_url(kind: str, base_url: str) -> None:
    """Where a key may be sent: https, or plain http only to a local/LAN server."""
    if kind == "openai_compat":
        try:
            url = urllib.parse.urlparse(base_url)
            url.port  # noqa: B018 - raises ValueError on a malformed port
        except ValueError:
            raise LLMError("That base URL is not valid.") from None
        if url.scheme not in ("http", "https") or not url.hostname:
            raise LLMError("Enter the provider's base URL, e.g. https://api.openai.com/v1")
        if url.scheme == "http" and not Provider(id="", name="", kind=kind, model="x", base_url=base_url).is_local:
            raise LLMError("Use https for internet providers (plain http only for local/LAN servers).")
    if kind == "anthropic" and base_url and not base_url.startswith("https://"):
        raise LLMError("Claude API base URL must use https.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies the Authorization header onto a redirect, even to another host: never follow one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirected to {newurl}; not following it with the key",
                                     headers, fp)


_opener = urllib.request.build_opener(_NoRedirect)


def complete(p: Provider, system: str, messages: list[dict]) -> dict:
    """Run one turn. messages: [{"role": "user"|"assistant", "content": str}], last one from the user.

    Returns {"text", "model", "sources": [{"title", "url"}]}.
    """
    validate(p)
    if not messages or messages[-1]["role"] != "user":
        raise LLMError("The conversation must end with a user message.")
    if p.kind == "claude_code":
        return _claude_code(p, system, messages)
    if p.kind == "anthropic":
        return _anthropic(p, system, messages)
    return _openai_compat(p, system, messages)


# ---------------------------------------------------------------- Claude Code CLI (subscription login)
def sweep_system_files() -> None:
    """Delete prompt files a killed call left behind (the normal path deletes its own in `finally`)."""
    for leftover in WORK_DIR.glob("system-*.md") if WORK_DIR.is_dir() else []:
        try:
            leftover.unlink()
        except OSError as exc:
            log.warning("could not delete %s: %s", leftover, exc)


def _claude_code(p: Provider, system: str, messages: list[dict]) -> dict:
    cli = find_claude_cli()
    if cli is None:
        raise LLMError("Claude Code is not installed. Connect Claude in Settings or choose another model.")
    from analyzer.claude_install import update_if_needed
    update_if_needed()  # after a version bump this answer still uses the previous binary
    transcript = []
    for m in messages[:-1]:
        transcript.append(f"<{m['role']}_turn>\n{m['content']}\n</{m['role']}_turn>")
    prompt = (("Earlier in this conversation:\n" + "\n".join(transcript) + "\n\n") if transcript else "") + messages[-1]["content"]
    # The prompt carries text nobody here wrote (news headlines, names, platforms). An "@path" in it must
    # never become a Claude Code file mention, so "@" goes as the look-alike FULLWIDTH COMMERCIAL AT; the
    # answer gets "@" back (links such as medium.com/@x keep working).
    prompt = prompt.replace("@", "＠")
    tools = "WebSearch,WebFetch" if p.web_research else ""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    # The system prompt (persona + rules, up to ~27k characters) goes through a private temp file, not
    # argv: Windows caps a command line at 32,767 characters (8,191 through a .cmd shim).
    fd, system_file = tempfile.mkstemp(prefix="system-", suffix=".md", dir=WORK_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(system)
        cmd = [cli, "-p", "--model", p.model, "--system-prompt-file", system_file, "--output-format", "json",
               "--tools", tools, "--safe-mode", "--strict-mcp-config", "--no-session-persistence",
               "--disable-slash-commands", "--no-chrome", "--permission-prompts", "none"]
        if p.web_research:
            cmd += ["--allowedTools", "WebSearch", "WebFetch"]
        if p.effort != "default":  # a level the model lacks runs at the nearest one below (Claude Code docs)
            cmd += ["--effort", p.effort]
        done = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT_SECONDS, cwd=WORK_DIR, env=cli_environment())
    except subprocess.TimeoutExpired:
        raise LLMError("Claude took longer than 5 minutes. Try a shorter question.") from None
    except OSError as exc:
        raise LLMError(f"Could not start Claude Code: {exc}") from None
    finally:
        try:
            os.unlink(system_file)
        except OSError as exc:
            log.warning("could not delete %s: %s", system_file, exc)
    try:
        text = parse_cli_output(done.returncode, done.stdout, done.stderr).replace("＠", "@")
    except RuntimeError as exc:
        raise LLMError(str(exc)) from None
    return {"text": text, "model": p.model, "sources": _links(text)}


# ---------------------------------------------------------------- Claude API (official SDK)
def _anthropic(p: Provider, system: str, messages: list[dict]) -> dict:
    import anthropic

    key = secrets_store.get(p.secret_name)
    if not key:
        raise LLMError("No API key saved for this provider.")
    client = anthropic.Anthropic(api_key=key, base_url=p.base_url or None, timeout=TIMEOUT_SECONDS, max_retries=2)
    # The system prompt holds nothing time-dependent, so it is marked for caching: a follow-up within the
    # cache lifetime reads it instead of paying for it again (below the model's minimum cacheable length
    # it is simply not cached).
    params: dict = {"model": p.model, "max_tokens": MAX_TOKENS,
                    "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    "messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
    if p.model in EFFORT_MODELS and p.effort != "default":
        params["output_config"] = {"effort": p.effort}
    if p.model in FALLBACK_MODELS:
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"  # a declined request is retried on Anthropic's recommended model
    if p.can_browse:
        params["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    try:
        response = client.beta.messages.create(**params)
        for _ in range(5):  # server-side tool loop paused: resend to let it continue
            if response.stop_reason != "pause_turn":
                break
            params["messages"] = params["messages"][:len(messages)] + [{"role": "assistant", "content": response.content}]
            response = client.beta.messages.create(**params)
    except anthropic.AuthenticationError:
        raise LLMError("The Claude API rejected the key. Check it in Settings.") from None
    except anthropic.PermissionDeniedError:
        raise LLMError("This API key may not use that model.") from None
    except anthropic.NotFoundError:
        raise LLMError(f"Model not found: {p.model}") from None
    except anthropic.RateLimitError:
        raise LLMError("Rate limited by the Claude API. Try again in a minute.") from None
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from None
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach the Claude API (network).") from None
    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request. Rephrase the question.")
    texts, sources = [], []
    for block in response.content:
        if getattr(block, "type", None) != "text":
            continue
        texts.append(block.text)
        for cite in getattr(block, "citations", None) or []:
            url = getattr(cite, "url", None)
            if url and all(s["url"] != url for s in sources):
                sources.append({"title": getattr(cite, "title", None) or url, "url": url})
    text = "".join(texts).strip()
    if not text:
        raise LLMError("The model returned no text.")
    return {"text": text, "model": getattr(response, "model", p.model), "sources": sources or _links(text)}


# ---------------------------------------------------------------- OpenAI-compatible (cloud + local)
def _openai_compat(p: Provider, system: str, messages: list[dict]) -> dict:
    key = secrets_store.get(p.secret_name)
    headers = {"Content-Type": "application/json", "User-Agent": "moneylyzer"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = json.dumps({
        "model": p.model,
        "messages": [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"]} for m in messages],
        "stream": False,
    }).encode("utf-8")
    url = p.base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = _error_message(exc.read().decode("utf-8", "replace"))
        if exc.code in (400, 401, 403) and re.search(r"api.?key|auth", detail, re.I):
            raise LLMError(f"The provider rejected the API key ({exc.code}): {detail}") from None
        raise LLMError(f"Provider error {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        hint = " Is the local server running and reachable from the container (host.docker.internal)?" if p.is_local else ""
        raise LLMError(f"Could not reach {p.base_url}: {exc.reason}.{hint}") from None
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise LLMError(f"Bad or slow response from {p.base_url}: {exc}") from None
    try:
        text = (data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LLMError("Unexpected response shape (expected choices[0].message.content).") from None
    if not text:
        raise LLMError("The model returned no text.")
    return {"text": text, "model": data.get("model") or p.model, "sources": _links(text)}


# ---------------------------------------------------------------- model lists
MAX_LISTED_MODELS = 500


KEY_RE = re.compile(r"[\x21-\x7e]{1,500}")  # printable ASCII, no spaces: what an API key looks like


def _clean_ids(raw_ids, *, keep_order: bool) -> dict:
    ids = list(dict.fromkeys(i for i in raw_ids if isinstance(i, str) and MODEL_RE.fullmatch(i)))
    if not keep_order:
        ids.sort()
    return {"models": ids[:MAX_LISTED_MODELS], "total": len(ids)}


def list_models(kind: str, base_url: str, key: str | None) -> dict:
    """The provider's own model list, for the Model suggestions: {"models": [...], "total": n}.

    Claude login has none (fixed aliases). The same URL rules as saving apply, so a key never
    travels over plain http to the internet, and no redirect is followed with it.
    """
    if key is not None and not KEY_RE.fullmatch(key):
        raise LLMError("That API key has spaces or unusual characters; paste it again.")
    check_base_url(kind, base_url)
    if kind == "anthropic":
        import anthropic

        if not key:
            raise LLMError("Enter the API key first; the Claude API lists models only for a key.")
        client = anthropic.Anthropic(api_key=key, base_url=base_url or None, timeout=15, max_retries=0)
        try:
            raw_ids = [getattr(m, "id", None) for _, m in zip(range(MAX_LISTED_MODELS * 2), client.models.list())]
        except anthropic.AuthenticationError:
            raise LLMError("The Claude API rejected the key.") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {str(exc.message)[:300]}") from None
        except anthropic.APIConnectionError:
            raise LLMError("Could not reach the Claude API (network).") from None
        except anthropic.APIError:
            raise LLMError("The Claude API sent a model list this app could not read.") from None
        return _clean_ids(raw_ids, keep_order=True)  # newest first, as the API returns them
    if kind != "openai_compat":
        raise LLMError("Claude login has no model list; use one of the names in the explainer.")
    headers = {"User-Agent": "moneylyzer", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(base_url.rstrip("/") + "/models", headers=headers)
    try:
        with _opener.open(request, timeout=15) as resp:
            raw = resp.read(2_000_001)
        if len(raw) > 2_000_000:
            raise LLMError("The provider's model list is too large to show.")
        data = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise LLMError(f"The provider redirected the request ({exc.code}); check the base URL.") from None
        detail = _error_message(exc.read(20_000).decode("utf-8", "replace"))
        if exc.code in (401, 403):
            raise LLMError(f"The provider refused to list models without a valid API key ({exc.code}): {detail}") from None
        raise LLMError(f"The provider did not list its models ({exc.code}): {detail}") from None
    except urllib.error.URLError as exc:
        raise LLMError(f"Could not reach {base_url}: {exc.reason}.") from None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # timeouts, dropped connections, not an HTTP server, bad JSON; the message never echoes headers
        raise LLMError(f"Bad or slow response from {base_url} ({type(exc).__name__}).") from None
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LLMError("Unexpected response shape (expected a data list of models).")
    return _clean_ids((m.get("id") for m in items if isinstance(m, dict)), keep_order=False)


def _error_message(raw: str) -> str:
    """Providers disagree on error shapes: {"error":{"message"}}, {"detail"}, [{"error":...}], {"error": "..."}."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:300] or "no details"
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        err = data.get("error", data.get("detail", data))
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(err)[:300]
    return str(data)[:300]


def _links(text: str) -> list[dict]:
    seen, out = set(), []
    for url in re.findall(r"https?://[^\s)\]>\"']+", text):
        url = url.rstrip(".,;")
        if url not in seen:
            seen.add(url)
            out.append({"title": url, "url": url})
    return out[:20]
