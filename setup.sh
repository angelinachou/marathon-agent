#!/usr/bin/env bash
# One-time setup of YOUR private copy of marathon-agent. Needs the GitHub CLI (`gh auth login`).
#   ./setup.sh                 -> uses <your-login>/marathon-agent-private
#   ./setup.sh owner/repo      -> any private repo name you like
set -euo pipefail
cd "$(dirname "$0")"
TEMPLATE=angelinachou/marathon-agent
REPO=${1:-"$(gh api user --jq .login)/marathon-agent-private"}

if [ ! -f profile.yaml ]; then
  cp profile.example.yaml profile.yaml
  echo "Created profile.yaml. Fill in your details, then run ./setup.sh again."
  exit 1
fi

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  echo "Creating private repo $REPO from $TEMPLATE ..."
  gh repo create "$REPO" --private --template "$TEMPLATE"
fi
[ "$(gh repo view "$REPO" --json visibility --jq .visibility)" = "PRIVATE" ] || {
  echo "$REPO is not private. Refusing to store your details there."; exit 1; }

echo "Saving profile.yaml as the PROFILE secret ..."
gh secret set PROFILE -R "$REPO" < profile.yaml
if ! gh secret list -R "$REPO" | grep -q ANTHROPIC_API_KEY; then
  echo "Paste your Anthropic API key (from https://console.anthropic.com/settings/keys):"
  gh secret set ANTHROPIC_API_KEY -R "$REPO"
fi
gh variable set ENABLED -R "$REPO" --body true

echo "Sending a test email and running a first check ..."
sleep 5
gh workflow run marathon-agent -R "$REPO" -f command=test-email
gh workflow run marathon-agent -R "$REPO" -f command=check
echo "Done. Watch it at https://github.com/$REPO/actions"
