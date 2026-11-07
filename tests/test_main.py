"""The check -> apply -> email flow, with the network, browser and email faked."""

import pytest

from marathon_agent import check, config, main

RACES = {
    "berlin": {
        "name": "Berlin Marathon",
        "entry": "lottery",
        "check_url": "https://example.com/b",
        "domains": ["example.com"],
        "timezone": "Europe/Berlin",
        "max_charge": {"amount": 225, "currency": "EUR"},
    }
}
OPEN = {
    "status": "open",
    "edition_year": 2027,
    "opens": "2026-10-01",
    "closes": "2099-11-12",
    "apply_url": "https://example.com/apply",
    "quote": "q",
    "hash": "h",
    "answer": {},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(main, "load_races", lambda: RACES)
    monkeypatch.setattr(main, "load_profile", dict)
    sent, applied = [], []
    monkeypatch.setattr(main.mail, "send", lambda p, subject, body, image=None: sent.append(subject))
    obs = {"value": OPEN}
    monkeypatch.setattr(main, "observe", lambda race, prev: (obs["value"], 0.0))
    outcome = {"status": "applied"}

    def fake_apply(rid, race, profile, start, year, **kw):
        applied.append((rid, start, year))
        if outcome["status"] == "applied":
            kw["on_submit"]()
        return {
            "cost_usd": 1.5,
            "summary": "done",
            "confirmation": "C1",
            "final_url": start,
            "screenshot": None,
            **outcome,
        }

    monkeypatch.setattr(main, "apply", fake_apply)
    return NS(sent=sent, applied=applied, obs=obs, outcome=outcome)


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_open_lottery_is_applied_once_and_emailed(env):
    assert main.main(["check"]) == 0
    assert env.applied == [("berlin", "https://example.com/apply", 2027)]
    assert env.sent == ["✅ Applied: Berlin Marathon 2027"]
    main.main(["check"])  # next run: already applied, nothing happens
    assert len(env.applied) == 1 and len(env.sent) == 1
    state = config.load_state()
    assert state["races"]["berlin"]["editions"]["2027"]["result"] == "applied"
    assert state["spend_usd"]


def test_failure_retries_three_times_then_stops(env):
    env.outcome["status"] = "failed"
    for _ in range(5):
        main.main(["check"])
    assert len(env.applied) == 3
    assert "I won't retry" in env.sent[-1]


def test_captcha_block_is_not_retried(env):
    env.outcome["status"] = "blocked"
    main.main(["check"])
    main.main(["check"])
    assert len(env.applied) == 1


def test_closed_or_unknown_never_applies(env):
    for status in ("closed", "upcoming", "unknown"):
        env.obs["value"] = OPEN | {"status": status}
        main.main(["check"])
    assert env.applied == [] and env.sent == []


def test_unreadable_site_is_not_marked_closed(env, monkeypatch):
    def boom(race, prev):
        raise check.Blocked("HTTP 403")

    monkeypatch.setattr(main, "observe", boom)
    assert main.main(["check"]) == 1  # every race failed -> the workflow run fails
    rs = config.load_state()["races"]["berlin"]
    assert "status" not in rs and "403" in rs["error"]


def test_manual_applied_stops_auto_apply(env):
    main.main(["applied", "berlin", "2027"])
    main.main(["check"])
    assert env.applied == []


def test_dry_run_saves_and_sends_nothing(env):
    main.main(["check", "--dry-run"])
    assert env.applied == [] and env.sent == [] and not config.STATE_FILE.exists()
