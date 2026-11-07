# marathon-agent

Watches the Abbott World Marathon Majors and **applies to their lotteries for you** the day they
open, then emails you the confirmation. It runs on GitHub Actions, so your computer can be off.

```
✅ Applied: Berlin Marathon 2027
Confirmation: 1234567
Lottery closes: 2026-11-12   ·   AI cost: $2.40
```

## How it works

1. **Twice a day** it reads each race's official entry page. A small AI model (Claude Haiku)
   reads the page, but its answer only counts if the sentence it quotes is really on the page,
   the edition year is current and the dates fit. It's only called when a page changes
   (pennies a month).
2. **When a lottery is open**, a headless browser driven by Claude (Opus) logs in or creates your
   account (reading verification codes from your Gmail), fills the form from your profile,
   accepts the terms and submits.
3. **It emails you** the result with a screenshot: applied, or why it couldn't, with the link.
   If it can't apply, it retries (up to 3 times), then reminds you 7, 2 and 1 days before close.

| Race | Entry route it applies for |
|---|---|
| Tokyo | General Entry lottery (overseas) |
| London | Public ballot (international) |
| Berlin | Marathon lottery |
| Chicago | Non-guaranteed drawing (U.S. resident) |
| New York City | Non-guaranteed drawing |
| Sydney | Public ballot |
| Cape Town | Public ballot (8th Major since June 2026) |

Boston isn't included: it has no lottery. Edit [races.yaml](races.yaml) to change routes, add a
race (e.g. Shanghai, if it becomes a Major) or remove one.

## Set up your own (about 10 minutes)

1. Click **Use this template**, or just clone this repo, then run `./setup.sh`. It copies
   `profile.example.yaml` to `profile.yaml`.
2. Fill in `profile.yaml`: your details, a password for race accounts, your card and a
   [Gmail app password](https://myaccount.google.com/apppasswords) (needs 2-Step Verification).
3. Get an API key at [console.anthropic.com](https://console.anthropic.com/settings/keys) and set
   a monthly spend limit there (e.g. $20).
4. Run `./setup.sh` again. It creates a **private** repo for you, stores your profile and key as
   encrypted GitHub secrets, sends a test email and runs a first check.

Your details live only in GitHub secrets on your private repo (and in the git-ignored
`profile.yaml` on your machine). State is saved to a `state` branch of your private repo.

## Commands

In your private repo: **Actions → marathon-agent → Run workflow**, then type a command:

| Command | What it does |
|---|---|
| `check` | Check every race; apply wherever a lottery is open (this is what runs on schedule) |
| `rehearse berlin` | Go through Berlin's application but **stop before submitting** (good first test) |
| `apply berlin` | Apply to Berlin now |
| `applied berlin 2027` | Tell it you applied yourself, so it stops trying and reminding |
| `status` | Print what it knows |
| `test-email` | Send yourself a test email |

Locally: `uv run marathon-agent <command>` (add `--dry-run` to not save or send anything, `--show` to
watch the browser).

## Cost

About $1–3/month averaged over a year: GitHub Actions is free for this, page checks cost
pennies, and each application costs roughly $1–4 of Claude usage. Hard limits: $8 per
application, `MONTHLY_BUDGET_USD` (default $20, tracked in state) and your Console limit.

## What it will and won't do

- It accepts race terms and waivers and submits for you. It enters your card only after
  declaring the charge, and only up to each race's `max_charge` in `races.yaml`. It never
  buys extras, insurance or donations.
- It **won't solve CAPTCHAs or get around bot protection.** If a site shows one, you get an
  email with the link and a reminder schedule instead. Waiting rooms (queues) are fine.
- It never applies twice to the same race and year. If it clicked submit but saw no
  confirmation, it tells you to check rather than trying again.
- Passwords, card details and your Gmail password are never shown to the AI. It types
  placeholders that are filled in inside the browser.
- Some races' terms forbid automated entries; using this is your call.

## Troubleshooting

- **"Can't read the X page for 2+ days" email**: the race moved its page. Update `check_url`
  in `races.yaml`.
- **Run failed (GitHub emails you)**: every page was unreadable, usually a missing/invalid
  `ANTHROPIC_API_KEY`. Check the run log.
- **Scheduled runs are late**: GitHub may delay scheduled runs by up to an hour or so. Two
  runs a day covers it; lottery windows last days to weeks.

## Development

```
uv sync && uv run playwright install chromium
uv run pytest      # includes a real browser run against a local mock form
uv run ruff check .
```

About 750 lines in [src/marathon_agent](src/marathon_agent): `check.py` (read pages),
`apply.py` (browser agent), `main.py` (commands), `mail.py`, `config.py`.

MIT licensed.
