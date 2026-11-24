"""Optional AI explanation via the locally installed Claude Code CLI.

The app runs `claude -p` (headless Claude Code) on the user's own Claude plan.
This is the only part of the app that sends data off the PC: the selected
instrument's name, type and currency plus the already-computed metrics and
scenario ranges go to Anthropic. Quantities, cost basis, the SQLite database
and imported CSV files are never sent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from analyzer.db import DATA_DIR

# Empty working directory for the CLI, so it never runs inside the project folder.
WORK_DIR = DATA_DIR / "claude-cwd"
CLAUDE_MODELS = ("opus", "fable", "sonnet")
TIMEOUT_SECONDS = 300
# Only these computed metrics are sent; anything analyze_history may add later stays local.
SENT_METRIC_KEYS = (
    "observations", "as_of", "latest_price", "return_1m", "return_3m", "return_6m", "return_1y",
    "drawdown", "annual_volatility", "rsi_14", "sma_50", "sma_200", "score", "signal",
    "reasons", "failed_checks", "signal_note",
)
# Variables that would route the CLI to pay-per-token billing or another endpoint instead of the Claude plan.
BILLING_ENV_VARS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
)

SYSTEM_PROMPT = (
    "You are a cautious investment research assistant. Explain only the supplied metrics. "
    "Do not invent company fundamentals, news, prices, analyst ratings, valuation ratios, or facts not present. "
    "Distinguish measured historical data from modelled scenarios. Mention missing data. "
    "Do not issue personalized financial advice or present BUY/SELL labels as instructions. "
    "Explain risks, assumptions and what additional evidence would be needed. "
    "Keep the answer concise and format it as Markdown."
)


def find_claude_cli() -> str | None:
    """Locate the Claude Code CLI: CLAUDE_CLI env var, then PATH, then the native installer's default path."""
    override = os.environ.get("CLAUDE_CLI")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    default = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    if default.exists():
        return str(default)
    from analyzer.claude_install import installed_path  # downloaded on first use into the data volume
    return installed_path()


def build_command(cli: str, model: str) -> list[str]:
    return [
        cli,
        "-p",
        "--model", model,
        "--system-prompt", SYSTEM_PROMPT,
        "--output-format", "json",
        "--tools", "",               # text only: no file, shell or web tools
        "--safe-mode",               # no CLAUDE.md, hooks, plugins, skills or MCP servers
        "--strict-mcp-config",
        "--no-session-persistence",  # no transcript written to ~/.claude
        "--disable-slash-commands",
        "--no-chrome",
    ]


def cli_environment() -> dict[str, str]:
    env = dict(os.environ)
    # Use the signed-in Claude plan, never a pay-per-token API key that happens to be set.
    for key in (*BILLING_ENV_VARS, "CLAUDECODE"):
        env.pop(key, None)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"  # no CLI telemetry or error reporting
    return env


def build_prompt(instrument: dict, price_currency: str | None, metrics: dict, scenarios: list[dict]) -> str:
    payload = {
        "instrument": {
            "name": instrument.get("name"),
            "asset_type": instrument.get("asset_type"),
            "holding_currency": instrument.get("currency"),
            "price_currency": price_currency,
        },
        "computed_metrics": {key: metrics[key] for key in SENT_METRIC_KEYS if key in metrics},
        "historical_bootstrap_scenarios": scenarios,
    }
    # Escape "@" so a name like "Fund @C:/file.csv" is never read as a file mention. Still valid JSON.
    body = json.dumps(payload, ensure_ascii=False, default=str).replace("@", "\\u0040")
    return "Explain this investment snapshot:\n" + body


PORTFOLIO_ROW_KEYS = ("name", "type", "currency", "weight", "pnl_pct", "today_pct")


def build_portfolio_prompt(rows: list[dict]) -> str:
    """Relative figures only: weight in the portfolio and percentage changes, never amounts or quantities."""
    holdings = [
        {k: (round(r[k], 4) if isinstance(r.get(k), float) else r.get(k)) for k in PORTFOLIO_ROW_KEYS}
        for r in rows
    ]
    body = json.dumps({"holdings": holdings}, ensure_ascii=False, default=str).replace("@", "\\u0040")
    return (
        "Explain this portfolio's composition and today's moves in a few short paragraphs. "
        "weight = share of total value, pnl_pct = unrealized return since purchase, today_pct = change today "
        "(all fractions, null = unknown).\n" + body
    )


def explain_metrics(model: str, instrument: dict, price_currency: str | None, metrics: dict, scenarios: list[dict]) -> str:
    """Send only computed, user-selected metrics to Claude and return its Markdown explanation."""
    return _run_claude(model, build_prompt(instrument, price_currency, metrics, scenarios))


def explain_portfolio(model: str, rows: list[dict]) -> str:
    return _run_claude(model, build_portfolio_prompt(rows))


def _run_claude(model: str, prompt: str) -> str:
    if model not in CLAUDE_MODELS:
        raise ValueError(f"Unsupported model '{model}'. Choose one of: {', '.join(CLAUDE_MODELS)}.")
    cli = find_claude_cli()
    if cli is None:
        raise RuntimeError("Claude Code CLI not found. Install Claude Code or set CLAUDE_CLI to the path of claude.exe.")

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        build_command(cli, model),
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_SECONDS,
        cwd=WORK_DIR,
        env=cli_environment(),
    )
    return parse_cli_output(completed.returncode, completed.stdout, completed.stderr)


def parse_cli_output(returncode: int, stdout: str, stderr: str) -> str:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        detail = (stderr or stdout or "no output").strip()[-500:]
        raise RuntimeError(f"Claude Code exited with code {returncode}: {detail}") from None
    if isinstance(data, list):  # some configurations print the whole message list
        data = next((m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), None)
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected output from Claude Code (exit code {returncode}).")
    if returncode != 0 or data.get("is_error"):
        raise RuntimeError(f"Claude Code reported an error: {str(data.get('result') or data)[:500]}")
    return data.get("result") or "Claude returned no text."
