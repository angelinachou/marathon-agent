"""Send yourself email and read recent race emails, via Gmail."""

import email
import email.policy
import imaplib
import re
import smtplib
from datetime import UTC, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from .config import now


def send(profile: dict, subject: str, body: str, image: bytes | None = None) -> None:
    gmail = profile.get("gmail") or {}
    if not gmail.get("app_password"):
        print(f"[email not configured] {subject}\n{body}\n")
        return
    msg = EmailMessage()
    msg["From"] = msg["To"] = gmail["address"]
    msg["Subject"] = subject
    msg.set_content(body)
    if image:
        msg.add_attachment(image, maintype="image", subtype="png", filename="page.png")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(gmail["address"], gmail["app_password"])
        smtp.send_message(msg)


def _text(msg: email.message.Message) -> str:
    part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else msg
    body = part.get_content() if part else ""
    body = re.sub(r"<[^>]+>", " ", body)  # crude HTML strip is enough for codes and links
    return re.sub(r"[ \t]+", " ", body)


def recent(profile: dict, domains: list[str], minutes: int = 30, chars: int = 3000) -> list[dict]:
    """Emails from any of `domains` (sender contains it) in the last `minutes`, newest first."""
    gmail = profile.get("gmail") or {}
    if not gmail.get("app_password"):
        return []
    try:
        return _recent(gmail, domains, minutes, chars)
    except (imaplib.IMAP4.error, OSError) as e:  # never let email trouble stop an application
        print(f"Warning: couldn't read Gmail ({e}). Check gmail.app_password in your profile.")
        return []


def _recent(gmail: dict, domains: list[str], minutes: int, chars: int) -> list[dict]:
    since = now() - timedelta(minutes=minutes)
    found = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(gmail["address"], gmail["app_password"])
        imap.select("INBOX", readonly=True)
        nums = set()
        for domain in domains:
            _, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"), "FROM", domain)
            nums.update(data[0].split()[-10:])
        for num in nums:
            _, raw = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(raw[0][1], policy=email.policy.default)
            when = parsedate_to_datetime(msg["Date"]) if msg["Date"] else now()
            if when.tzinfo is None:  # some senders omit the timezone
                when = when.replace(tzinfo=UTC)
            if when >= since:
                found.append(
                    {
                        "from": str(msg.get("From", "")).lower(),
                        "subject": str(msg["Subject"]),
                        "date": when.isoformat(),
                        "body": _text(msg)[:chars],
                    }
                )
    return sorted(found, key=lambda m: m["date"], reverse=True)
