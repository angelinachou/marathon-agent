from datetime import UTC, datetime

import pytest

from marathon_agent import check

RACE = {
    "name": "Berlin Marathon",
    "entry": "lottery",
    "check_url": "https://www.example-berlin.com/reg",
    "domains": ["example-berlin.com"],
    "timezone": "Europe/Berlin",
}
PAGE = (
    "Registration period for the lottery: October 1, 2026 to November 12, 2026. "
    "Announcement of lottery results: December 3, 2026."
)
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
QUOTE = "Registration period for the lottery: October 1, 2026 to November 12, 2026."


def answer(**kw):
    base = {
        "status": "open",
        "edition_year": 2027,
        "opens": "2026-10-01",
        "closes": "2026-11-12",
        "apply_url": "https://www.example-berlin.com/apply",
        "quote": QUOTE,
    }
    return base | kw


def test_open_window_is_open():
    r = check.verify(answer(), RACE, PAGE, NOW)
    assert r["status"] == "open"
    assert r["apply_url"] == "https://www.example-berlin.com/apply"


def test_quote_not_on_page_is_never_open():
    r = check.verify(answer(quote="Applications are open now!"), RACE, PAGE, NOW)
    assert r["status"] == "unknown"


def test_stale_previous_edition_is_not_open():
    page = "Runners can apply to the 2026 drawing during the application period from February 4–25."
    a = answer(
        edition_year=2026,
        opens="2026-02-04",
        closes="2026-02-25",
        quote="Runners can apply to the 2026 drawing during the application period from February 4–25.",
    )
    assert check.verify(a, RACE, page, NOW)["status"] != "open"


def test_missing_year_is_not_open():
    assert check.verify(answer(edition_year=None), RACE, PAGE, NOW)["status"] == "unknown"


def test_past_close_date_is_closed():
    later = datetime(2026, 11, 13, 12, tzinfo=UTC)
    assert check.verify(answer(), RACE, PAGE, later)["status"] == "closed"


def test_close_date_counts_whole_day_in_race_timezone():
    # 22:00 UTC on Nov 12 is still Nov 12 (23:00) in Berlin
    late = datetime(2026, 11, 12, 22, tzinfo=UTC)
    assert check.verify(answer(), RACE, PAGE, late)["status"] == "open"


def test_future_open_date_is_upcoming():
    early = datetime(2026, 9, 20, tzinfo=UTC)
    assert check.verify(answer(), RACE, PAGE, early)["status"] == "upcoming"


def test_upcoming_becomes_open_when_window_starts():
    assert check.verify(answer(status="upcoming"), RACE, PAGE, NOW)["status"] == "open"


def test_unofficial_apply_link_is_dropped():
    r = check.verify(answer(apply_url="https://evil.example.net/berlin"), RACE, PAGE, NOW)
    assert r["apply_url"] is None
    assert not check.official("http://www.example-berlin.com/x", RACE["domains"])  # https only


def test_blocked_site_raises(monkeypatch):
    class Resp:
        status_code, url, text = 403, "https://www.example-berlin.com/reg", "Access denied"

    monkeypatch.setattr(check.httpx, "get", lambda *a, **k: Resp())
    with pytest.raises(check.Blocked):
        check.fetch(RACE["check_url"])


def test_unchanged_page_skips_the_model(monkeypatch):
    monkeypatch.setattr(check, "fetch", lambda url: (PAGE, []))
    calls = []

    def classifier(race, text, links):
        calls.append(1)
        return answer(), 0.001

    first, _spent = check.observe(RACE, {}, classifier)
    second, spent2 = check.observe(RACE, first, classifier)
    assert len(calls) == 1 and spent2 == 0
    assert second["hash"] == first["hash"]
