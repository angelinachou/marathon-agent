"""Drive the real browser tools with a scripted fake model against a local mock form."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from marathon_agent import apply as ap

FORM = (Path(__file__).parent / "fixtures" / "mock_form.html").resolve().as_uri()
RACE = {
    "name": "Mock Marathon",
    "entry": "lottery",
    "domains": ["example.com"],
    "max_charge": {"amount": 260, "currency": "USD"},
    "timezone": "UTC",
}
PROFILE = {
    "runner": {
        "first_name": "Alex",
        "last_name": "Example",
        "date_of_birth": "1990-01-31",
        "country_of_residence": "United States",
        "tshirt_size": "S",
    },
    "password": "hunter2-secret",
    "card": {"number": "4111111111111111"},
    "gmail": {"address": "a@example.com", "app_password": "gmail-secret"},
}


def find(snapshot: str, label: str) -> str:
    for line in snapshot.splitlines():
        if line.startswith('{"id"'):
            item = json.loads(line)
            if label.lower() in (item["label"] + " " + item["text"]).lower():
                return item["id"]
    raise AssertionError(f"no control labelled {label!r}")


class ScriptedModel:
    """Plays the role of Claude: each step reads the latest snapshot and calls one tool."""

    def __init__(self, steps):
        self.steps, self.seen = steps, []
        self.messages = NS(create=self.create)

    def create(self, messages, **_):
        last = messages[-1]["content"]
        text = last if isinstance(last, str) else last[-1]["content"]
        self.seen.append(text)
        name, args = self.steps.pop(0)(text)
        block = NS(type="tool_use", id=f"t{len(self.seen)}", name=name, input=args)
        usage = NS(
            input_tokens=1000, output_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=0
        )
        return NS(content=[block], stop_reason="tool_use", usage=usage)


def run(steps, **kw):
    model = ScriptedModel(steps)
    submits = []
    out = ap.apply("mock", RACE, PROFILE, FORM, 2027, client=model, on_submit=lambda: submits.append(1), **kw)
    return out, model, submits


def happy_path():
    return [
        lambda s: ("fill", {"id": find(s, "First name"), "text": "Alex"}),
        lambda s: ("fill", {"id": find(s, "Last name"), "text": "Example"}),
        lambda s: ("fill", {"id": find(s, "Date of birth"), "text": "1990-01-31"}),
        lambda s: ("select", {"id": find(s, "Country"), "option": "United States"}),
        lambda s: ("select", {"id": find(s, "T-shirt"), "option": "S"}),
        lambda s: ("check", {"id": find(s, "waiver"), "checked": True}),
        lambda s: ("fill", {"id": find(s, "Card number"), "text": "{{card.number}}"}),  # refused
        lambda s: ("declare_charge", {"amount": 500, "currency": "USD", "what": "entry"}),  # over cap
        lambda s: ("declare_charge", {"amount": 250, "currency": "USD", "what": "entry if selected"}),
        lambda s: ("fill", {"id": find(s, "Card number"), "text": "{{card.number}}"}),
        lambda s: ("submit", {"id": find(s, "Submit application")}),
        lambda s: (
            "done",
            {
                "status": "applied" if "MA-12345" in s else "failed",
                "confirmation": "MA-12345",
                "summary": "Applied.",
            },
        ),
    ]


def test_full_application_on_mock_form():
    out, model, submits = run(happy_path())
    assert out["status"] == "applied" and out["confirmation"] == "MA-12345"
    assert submits == [1]
    assert out["screenshot"]
    transcript = "\n".join(model.seen)
    assert "call declare_charge before entering card details" in transcript
    assert "above this race's limit" in transcript
    assert '"value": "(filled)"' in transcript  # card value masked after filling
    for secret in ("4111111111111111", "hunter2-secret", "gmail-secret"):
        assert secret not in transcript  # secrets never shown to the model
    assert "Alex" in model.seen[0]  # ordinary profile data is


def test_rehearsal_never_submits():
    steps = happy_path()[:-2] + [
        lambda s: ("submit", {"id": find(s, "Submit application")}),
        lambda s: (
            "done",
            {
                "status": "rehearsed",
                "confirmation": "",
                "summary": "Stopped before submit." if "MA-12345" not in s else "SUBMITTED",
            },
        ),
    ]
    out, _model, submits = run(steps, rehearse=True)
    assert out["status"] == "rehearsed" and out["summary"] == "Stopped before submit."
    assert submits == []


def test_goto_only_official_domains():
    steps = [
        lambda s: ("goto", {"url": "https://evil.example.net/"}),
        lambda s: ("done", {"status": "failed", "confirmation": "", "summary": s.split("\n")[0]}),
    ]
    out, _, _ = run(steps)
    assert "not an official domain" in out["summary"]


def test_crash_after_submit_is_reported_as_unconfirmed():
    steps = happy_path()[:-1] + [lambda s: ("done", {"status": "failed", "confirmation": "", "summary": "?"})]
    out, _, submits = run(steps)
    assert submits == [1] and out["status"] == "submitted_unconfirmed"


def test_profile_split_hides_only_secrets():
    visible, secrets = ap.split_profile(PROFILE | {"accounts": {"nyc": {"email": "x@y.z", "password": "p"}}})
    assert "gmail" not in visible
    assert visible["card"]["number"] == "{{card.number}}"
    assert visible["accounts"]["nyc"] == {"email": "x@y.z", "password": "{{accounts.nyc.password}}"}
    assert secrets["password"] == "hunter2-secret"
