"""marathon-agent: watch the World Marathon Majors and apply to lotteries when they open.

  marathon-agent check                 check every race; apply wherever a lottery is open
  marathon-agent apply RACE            apply to RACE now (uses the last seen apply link)
  marathon-agent rehearse RACE         go through RACE's application but stop before submitting
  marathon-agent applied RACE YEAR     record that you applied yourself (stops reminders)
  marathon-agent status                show what the agent knows
  marathon-agent test-email            send yourself a test email
Add --dry-run to check/apply without saving state or sending email, --show to watch the browser.
"""

import argparse
import math
import os
import sys
from datetime import datetime, timedelta

from . import mail
from .apply import apply
from .check import ApiDown, observe, parse_when, ping
from .config import (
    add_spend,
    api_problem,
    load_profile,
    load_races,
    load_state,
    now,
    over_budget,
    save_state,
)

FINISHED = {"applied", "already_applied", "submitted_unconfirmed"}
MAX_ATTEMPTS = 3


def api_alert(state, reason, notify):
    """Tell the user (at most weekly) that the API key or credit needs attention."""
    last = state.get("api_alerted_at")
    if last and now() - datetime.fromisoformat(last) < timedelta(days=7):
        return
    state["api_alerted_at"] = now().isoformat()
    repo = os.environ.get("GITHUB_REPOSITORY", "<your private repo>")
    notify(
        "🔑 marathon-agent needs a new Anthropic API key or credit",
        f"I can't use the AI because {reason}.\n\n"
        "Until this is fixed I'll still email you when a race's entry page changes, "
        "but I can't tell whether a lottery is open or apply for you.\n\n"
        "To fix:\n"
        "  1. Add credit: https://console.anthropic.com/settings/billing\n"
        "     or create a new key: https://console.anthropic.com/settings/keys\n"
        f"  2. If you made a new key, run:  gh secret set ANTHROPIC_API_KEY -R {repo}\n"
        "I'll email you again once it's working.",
    )


def api_ok(state, notify):
    if state.pop("api_alerted_at", None):
        notify("✅ marathon-agent: the Anthropic API is working again", "Back to normal checks.")


def edition_state(rs: dict, year) -> dict:
    return rs.setdefault("editions", {}).setdefault(str(year), {"attempts": 0, "reminded": []})


def run_apply(rid, race, rs, profile, state, *, rehearse=False, dry=False, show=False, notify=print):
    year = rs.get("edition_year")
    ed = edition_state(rs, year)
    start = rs.get("apply_url") or race["check_url"]
    name = f"{race['name']} {year or ''}".strip()
    closes = rs.get("closes") or "not stated"

    def apply_yourself(why):  # once per race+edition, so twice-daily runs don't repeat it
        if not ed.get("told_to_apply"):
            ed["told_to_apply"] = True
            notify(
                f"⚠️ {name} is OPEN — please apply yourself",
                f"I can't apply because {why}.\n\nApply here: {start}\nLottery closes: {closes}\n\n"
                f"Afterwards run the workflow with: applied {rid} {year}",
            )

    if over_budget(state):
        apply_yourself("this month's AI budget is used up")
        return
    if dry:
        print(f"[dry-run] would {'rehearse' if rehearse else 'apply to'} {rid} {year} from {start}")
        return

    def on_submit():
        ed.update(result="submitted_unconfirmed", at=now().isoformat())
        save_state(state)

    print(f"{'Rehearsing' if rehearse else 'Applying to'} {race['name']} {year} ...")
    try:
        out = apply(
            rid, race, profile, start, year, rehearse=rehearse, on_submit=on_submit, headless=not show
        )
    except Exception as e:  # noqa: BLE001 - report every crash instead of losing the run
        if ed.get("result") == "submitted_unconfirmed":  # crashed after clicking submit: never retry
            out = {"status": "submitted_unconfirmed", "summary": f"Crashed after submitting: {e}"[:400]}
        elif reason := api_problem(e):
            api_alert(state, reason, notify)
            apply_yourself(reason)
            return  # not counted as an attempt: it retries once the key/credit is fixed
        else:
            out = {"status": "failed", "summary": f"The agent crashed: {type(e).__name__}: {e}"[:400]}
        out |= {"cost_usd": 0, "confirmation": "", "final_url": start, "screenshot": None}
    add_spend(state, out["cost_usd"])
    status = out["status"]
    if not rehearse:
        ed["attempts"] += 1
        if status == "blocked":  # e.g. CAPTCHA or price above cap: retrying won't help
            ed["attempts"] = MAX_ATTEMPTS
        ed.update(result=status, at=now().isoformat(), confirmation=out.get("confirmation", ""))
    details = (
        f"{out['summary']}\n\nConfirmation: {out.get('confirmation') or '-'}\n"
        f"Lottery closes: {closes}\nLink: {start}\nLast page: {out.get('final_url')}\n"
        f"AI cost: ${out['cost_usd']}"
    )
    if status == "applied":
        subject = f"✅ Applied: {name}"
    elif status == "already_applied":
        subject = f"✅ Already applied: {name}"
    elif rehearse:
        subject = f"🧪 Rehearsal {'done' if status == 'rehearsed' else f'stopped ({status})'}: {name}"
    elif status == "submitted_unconfirmed":
        subject = f"❓ {name}: submitted but no confirmation seen — please check your account"
    else:
        left = MAX_ATTEMPTS - ed["attempts"]
        retry = f"I'll retry ({left} left)." if left > 0 else "I won't retry; please apply yourself."
        subject = f"⚠️ {name} is OPEN — I couldn't apply ({status}). {retry}"
        details += f"\n\nIf you apply yourself, run the workflow with: applied {rid} {year}"
    notify(subject, details, out.get("screenshot"))


def remind(rid, race, rs, notify):
    """7/2/1-day reminders for open lotteries the agent couldn't finish."""
    closes = parse_when(rs.get("closes"), race["timezone"], end_of_day=True)
    ed = edition_state(rs, rs.get("edition_year"))
    if not closes or ed.get("result") in FINISHED or ed["attempts"] < MAX_ATTEMPTS:
        return
    days = math.ceil((closes - now()) / timedelta(days=1))
    if days in (7, 2, 1) and days not in ed["reminded"]:
        ed["reminded"].append(days)
        notify(
            f"⏰ {race['name']} {rs.get('edition_year')} lottery closes in {days} day(s)",
            f"Closes: {closes:%Y-%m-%d %H:%M %Z}\nApply: {rs.get('apply_url') or race['check_url']}\n"
            f"Then run the workflow with: applied {rid} {rs.get('edition_year')}",
        )


def cmd_check(races, state, profile, args, notify) -> int:
    if problem := ping():
        api_alert(state, problem, notify)
    else:
        api_ok(state, notify)
    ok = 0
    for rid, race in races.items():
        if args.race and rid != args.race:
            continue
        rs = state.setdefault("races", {}).setdefault(rid, {})
        try:
            obs, spent = observe(race, rs)
        except ApiDown as e:  # page read fine, AI unavailable: fall back to "page changed" emails
            ok += 1
            print(f"{rid:9} NO-AI      {e.reason}")
            api_alert(state, e.reason, notify)
            if e.digest != (rs.get("seen_hash") or rs.get("hash")):
                notify(
                    f"📄 The {race['name']} entry page changed",
                    f"I can't read it without the AI ({e.reason}), so please take a look:\n"
                    f"{race['check_url']}",
                )
            rs["seen_hash"] = e.digest
            continue
        except Exception as e:  # noqa: BLE001 - one broken site must not stop the others
            print(f"{rid:9} UNREADABLE  {type(e).__name__}: {e}")
            rs["error"] = f"{type(e).__name__}: {e}"[:300]
            rs.setdefault("failing_since", now().isoformat())
            since = parse_when(rs["failing_since"], "UTC", False)
            if now() - since > timedelta(hours=48) and not rs.get("failure_alerted"):
                rs["failure_alerted"] = True
                notify(
                    f"🔧 Can't read the {race['name']} page for 2+ days",
                    f"{race['check_url']}\n{rs['error']}\nThe page may have moved; update races.yaml.",
                )
            continue
        ok += 1
        add_spend(state, spent)
        rs["seen_hash"] = obs["hash"]
        for k in ("error", "failing_since", "failure_alerted"):
            rs.pop(k, None)
        rs.update(
            {
                k: obs.get(k)
                for k in (
                    "status",
                    "edition_year",
                    "opens",
                    "closes",
                    "apply_url",
                    "quote",
                    "hash",
                    "answer",
                    "why",
                )
            }
        )
        rs["checked"] = now().date().isoformat()
        print(
            f"{rid:9} {obs['status'].upper():9} {obs.get('edition_year') or '':5} "
            f"closes={obs.get('closes') or '-'}  {obs.get('why') or ''}"
        )
        if obs["status"] == "open":
            ed = edition_state(rs, obs.get("edition_year"))
            if ed.get("result") not in FINISHED and ed["attempts"] < MAX_ATTEMPTS:
                run_apply(rid, race, rs, profile, state, dry=args.dry_run, show=args.show, notify=notify)
            remind(rid, race, rs, notify)
    return 0 if ok else 1


def cmd_status(races, state):
    for rid, race in races.items():
        rs = state.get("races", {}).get(rid, {})
        eds = ", ".join(f"{y}: {e.get('result', '-')}" for y, e in rs.get("editions", {}).items())
        print(
            f"{race['name']:24} {rs.get('status', 'never checked'):10} {rs.get('edition_year') or '':5} "
            f"closes={rs.get('closes') or '-':26} checked={rs.get('checked', '-')}  {eds}"
        )
        if rs.get("error"):
            print(f"{'':24} error: {rs['error']}")
    print(f"AI spend: {state.get('spend_usd', {})}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="marathon-agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("command", choices=["check", "apply", "rehearse", "applied", "status", "test-email"])
    ap.add_argument("race", nargs="?")
    ap.add_argument("year", nargs="?", type=int)
    ap.add_argument("--dry-run", action="store_true", help="don't save state, send email or submit")
    ap.add_argument("--show", action="store_true", help="show the browser window (local runs)")
    args = ap.parse_args(argv)

    races, state, profile = load_races(), load_state(), load_profile()
    if args.race and args.race not in races:
        ap.error(f"unknown race '{args.race}'. Choose from: {', '.join(races)}")

    def notify(subject, body, image=None):
        print(f"\n>>> {subject}\n{body}\n")
        if not args.dry_run:
            mail.send(profile, subject, body, image)

    code = 0
    if args.command == "check":
        code = cmd_check(races, state, profile, args, notify)
    elif args.command in ("apply", "rehearse"):
        if not args.race:
            ap.error(f"{args.command} needs a race")
        rs = state.setdefault("races", {}).setdefault(args.race, {})
        if args.year:
            rs["edition_year"] = args.year
        run_apply(
            args.race,
            races[args.race],
            rs,
            profile,
            state,
            rehearse=args.command == "rehearse",
            dry=args.dry_run,
            show=args.show,
            notify=notify,
        )
    elif args.command == "applied":
        if not (args.race and args.year):
            ap.error("usage: applied RACE YEAR")
        rs = state.setdefault("races", {}).setdefault(args.race, {})
        edition_state(rs, args.year).update(result="applied", at=now().isoformat(), confirmation="manual")
        print(f"Recorded: applied to {args.race} {args.year}.")
    elif args.command == "status":
        cmd_status(races, state)
        return 0
    elif args.command == "test-email":
        mail.send(profile, "🏃 marathon-agent test", "Email works. You'll hear from me when a lottery opens.")
        print("Sent." if profile.get("gmail") else "No gmail settings in profile; nothing sent.")
        return 0

    if not args.dry_run:
        save_state(state)
    return code


if __name__ == "__main__":
    sys.exit(main())
