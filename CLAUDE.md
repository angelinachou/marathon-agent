# marathon-agent

Watches World Marathon Majors entry pages and auto-applies to lotteries via a Claude-driven
Playwright browser on GitHub Actions. Keep it small and simple.

- Commands: `uv run pytest`, `uv run ruff check . && uv run ruff format .`, `uv run marathon-agent status`
- `races.yaml` is the race list; `profile.yaml` / `state.json` hold personal data and are git-ignored.
- This repo is PUBLIC. Never commit personal data. State lives on the `state` branch of the user's
  private copy (`marathon-agent-private`), never on `main`.
- Rules that must hold: a page is only "open" if `check.verify` accepts it; secrets reach the model
  only as `{{placeholders}}`; card fields need `declare_charge` within `max_charge`; at most one
  `submit` per race+edition; never bypass CAPTCHAs or bot protection.
- Models: `claude-haiku-5-5` for page checks, `claude-opus-5-5` for applying.
