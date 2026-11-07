"""Loading races, your profile and the saved state."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("MARATHON_AGENT_HOME", Path.cwd()))
STATE_FILE = ROOT / "state.json"

# USD per million tokens: (input, output). Cache writes cost 1.25x input, reads 0.1x.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}
MONTHLY_BUDGET_USD = float(os.environ.get("MONTHLY_BUDGET_USD", "20"))


def now() -> datetime:
    return datetime.now(UTC)


def load_races() -> dict:
    return yaml.safe_load((ROOT / "races.yaml").read_text())


def load_profile() -> dict:
    """PROFILE env var (GitHub secret) wins; otherwise a local profile.yaml."""
    text = os.environ.get("PROFILE")
    if not text and (ROOT / "profile.yaml").exists():
        text = (ROOT / "profile.yaml").read_text()
    return yaml.safe_load(text) if text else {}


def load_state() -> dict:
    if STATE_FILE.exists() and STATE_FILE.read_text().strip():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True, default=str) + "\n")


def cost(model: str, usage) -> float:
    inp, out = PRICES.get(model, (5.0, 25.0))
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        usage.input_tokens * inp + write * inp * 1.25 + read * inp * 0.1 + usage.output_tokens * out
    ) / 1e6


def add_spend(state: dict, usd: float) -> None:
    month = now().strftime("%Y-%m")
    spend = state.setdefault("spend_usd", {})
    spend[month] = round(spend.get(month, 0) + usd, 4)


def api_problem(e: Exception) -> str | None:
    """A plain-English reason if `e` means the Anthropic key/credit needs your attention."""
    import anthropic

    msg = str(e).lower()
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code == 401:
            return "your Anthropic API key is invalid, expired or revoked"
        if e.status_code == 402 or "credit balance" in msg or "billing" in msg:
            return "your Anthropic account is out of credit"
        if e.status_code == 403:
            return "your Anthropic API key isn't allowed to use the model"
    elif isinstance(e, anthropic.AnthropicError) and "api_key" in msg:
        return "no Anthropic API key is set"
    return None


def over_budget(state: dict) -> bool:
    return state.get("spend_usd", {}).get(now().strftime("%Y-%m"), 0) >= MONTHLY_BUDGET_USD
