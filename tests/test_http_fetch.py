"""The HTTP fetch path (curl_cffi) driven against saved real responses.

No network: a fake session factory replays saved Gumtree responses, so these
tests pin down the rotate-on-block policy, the challenge-page detection, and
the crawl loop (pagination, maxItems, dedupe, includeDescription, the auto-mode
handover to the browser) exactly as they will behave on the platform.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from src.http_fetch import PROFILE_LADDER, AVAILABLE_PROFILES, FetchError, HttpFetcher, available_ladder, extract_title
from src.main import Config, PendingWork, RunState, config_from_input, evaluate_run, run_http
from src.parser import BlockedPage, detect_block, looks_blocked

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH = "https://www.gumtree.com.au/s-rtx+4070/k0"
PAGE2 = "https://www.gumtree.com.au/s-rtx+4070/page-2/k0"
LOG = logging.getLogger("test")


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


CHALLENGE_HTML = load("peakhour_challenge.html")
LISTING_HTML = load("listing_1344519187.html")
# A slice of a real 200 page: the Peakhour *beacon* script that every real page carries.
REAL_PAGE_BEACON = (
    "<html><head><title>rtx 4070 | Gumtree Australia Local Classifieds</title></head><body>"
    "<script>rp.addInstrumentationInit('https://beacon.peakhour.io/beacon/','load','6aa4',false,false,'x');"
    "a+=\"&si=\"+rp.getCookie(\"PEAKHOUR_VISIT\");</script>" + "<div>" * 2000 + "</body></html>"
)


def search_html(ids: list[int], next_page: str | None, number_found: int | None = None) -> str:
    """A search page with window.APP_DATA embedded, the way Gumtree's default SRP serves it."""
    rows = [
        {"id": str(i), "title": f"RTX 4070 #{i}", "priceText": f"${i}", "priceType": "FIXED", "url": f"/web/listing/components/{i}",
         "location": "Dural", "locationState": "NSW", "age": "8 hours ago"}
        for i in ids
    ]
    search = {"results": {"main": rows, "top": []},
              "searchMeta": {"numberFound": number_found if number_found is not None else len(ids),
                             "zeroSearchResults": False,
                             "pagination": {"nextPageUrl": next_page, "isLastPage": next_page is None}}}
    return ("<html><head><title>rtx 4070 | Gumtree Australia Local Classifieds</title>"
            "<script>(function() {window.APP_DATA = " + json.dumps({"search": search}) + ";})();</script></head>"
            "<body>" + "<div class='x'></div>" * 600 + "</body></html>")


ZERO_HTML = ("<html><head><title>zzz | Gumtree Australia Local Classifieds</title><script>window.APP_DATA = "
             + json.dumps({"search": {"results": {"main": [], "top": []},
                                      "searchMeta": {"numberFound": 0, "zeroSearchResults": True, "pagination": {}}}})
             + ";</script></head><body>" + "<p>no</p>" * 1000 + "</body></html>")

SHELL_HTML = ("<html><head><title>rtx 4070 | Gumtree Australia Local Classifieds</title></head><body>"
              "<div id='react-root'><section class='search-results-page__user-ad-collection'></section></div>"
              + "<div></div>" * 1000 + "</body></html>")


# --------------------------------------------------------------------------- #
# Fake transport
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


CHALLENGE = FakeResponse(403, CHALLENGE_HTML, {"peakhour-challenge": "1", "content-type": "text/html; charset=utf-8"})
BLOCKED = FakeResponse(403, "", {"peakhour-error": "blocked", "content-length": "0"})
RATE_LIMITED = FakeResponse(429, "", {})


class FakeSession:
    def __init__(self, transport: "FakeTransport", profile: str, http_version: str, proxy_url: str | None):
        self.transport = transport
        self.profile = profile
        self.http_version = http_version
        self.proxy_url = proxy_url
        self.closed = False
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, headers=None):
        self.calls.append((url, dict(headers or {})))
        self.transport.calls.append((url, self.profile, self.http_version, self.proxy_url))
        script = self.transport.routes.get(url)
        if script is None:
            raise AssertionError(f"unexpected fetch of {url}")
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, Exception):
            raise step
        return step

    async def close(self):
        self.closed = True


class FakeTransport:
    """routes: url -> list of responses replayed in order (the last one repeats)."""

    def __init__(self, routes: dict[str, list]):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.sessions: list[FakeSession] = []
        self.sleeps: list[float] = []
        self.proxy_sessions: list[str] = []

    def factory(self, profile, http_version, proxy_url, timeout):
        s = FakeSession(self, profile, http_version, proxy_url)
        self.sessions.append(s)
        return s

    async def sleep(self, secs: float):
        self.sleeps.append(secs)

    def proxy_url_for(self, session_id: str):
        self.proxy_sessions.append(session_id)
        return f"http://session-{session_id}:pw@proxy.apify.com:8000"

    def fetcher(self, **kw) -> HttpFetcher:
        kw.setdefault("max_retries", 2)
        kw.setdefault("delay_secs", 1.0)
        return HttpFetcher(proxy_url_for=self.proxy_url_for, session_factory=self.factory, sleep=self.sleep, logger=LOG, **kw)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Block / challenge detection
# --------------------------------------------------------------------------- #
def test_profile_ladder_is_installed_and_starts_with_the_verified_profile():
    ladder = available_ladder()
    assert ladder and ladder[0] == ("safari18_0", "h2")
    assert all(p in AVAILABLE_PROFILES for p, _ in ladder)
    assert len(ladder) == len(PROFILE_LADDER)  # every ladder entry exists in the installed curl_cffi


def test_challenge_page_is_detected_by_header_and_by_body():
    assert detect_block(403, {"peakhour-challenge": "1"}, CHALLENGE_HTML, "") == "challenge"
    assert detect_block(403, {"Peakhour-Challenge": "1"}, "", "") == "challenge"  # header case-insensitive
    assert detect_block(403, {}, CHALLENGE_HTML, "") == "challenge"  # no header: the script body gives it away
    assert detect_block(200, {}, CHALLENGE_HTML, "") == "challenge"  # served with 200 (browser-side shell)
    assert "Peakhour-Challenge" in CHALLENGE_HTML and extract_title(CHALLENGE_HTML) == ""


def test_hard_block_rate_limit_and_denied_are_distinguished():
    assert detect_block(403, {"peakhour-error": "blocked"}, "", "") == "blocked"
    assert detect_block(403, {}, "", "") == "blocked"
    assert detect_block(429, {}, "", "") == "rate_limited"
    assert detect_block(503, {}, "", "") == "unavailable"
    assert detect_block(200, {}, "<html>Access has been denied to the requested page</html>", "Access denied") == "denied"


def test_real_pages_carry_peakhour_beacon_strings_and_are_not_blocked():
    """Regression: 'Peakhour'/'PEAKHOUR_VISIT' appear on every real 200 page (beacon script)."""
    assert "PEAKHOUR_VISIT" in REAL_PAGE_BEACON and "peakhour" in REAL_PAGE_BEACON
    assert detect_block(200, {"content-type": "text/html;charset=UTF-8"}, REAL_PAGE_BEACON, extract_title(REAL_PAGE_BEACON)) is None
    assert looks_blocked(200, "rtx 4070 | Gumtree Australia Local Classifieds", REAL_PAGE_BEACON) is False
    assert detect_block(200, {}, LISTING_HTML, "ASUS TUF Gaming RTX 4070 Ti") is None


# --------------------------------------------------------------------------- #
# Fetcher: rotation policy
# --------------------------------------------------------------------------- #
def test_fetch_ok_first_try_uses_first_profile_and_navigation_headers():
    t = FakeTransport({SEARCH: [FakeResponse(200, search_html([1, 2], None))]})
    f = t.fetcher()
    res = run(f.fetch(SEARCH))
    assert res.status == 200 and res.attempts == 1 and res.profile == "safari18_0" and res.http_version == "h2"
    assert res.title == "rtx 4070 | Gumtree Australia Local Classifieds"
    url, headers = t.sessions[0].calls[0]
    assert headers["Accept-Language"] == "en-AU,en;q=0.9"
    assert headers["Sec-Fetch-Site"] == "none" and headers["Sec-Fetch-Mode"] == "navigate" and "Referer" not in headers
    assert headers["Accept"].startswith("text/html,application/xhtml+xml")
    assert "sec-ch-ua" not in {k.lower() for k in headers}  # left to curl_cffi so it matches the impersonated UA
    assert t.sessions[0].proxy_url.startswith("http://session-s") and f.stats.blocked == 0
    assert t.sleeps == []  # no politeness pause before the very first request


def test_challenge_rotates_profile_proxy_session_and_cookies_then_succeeds():
    blocked_kinds: list[tuple[str, int | None]] = []
    t = FakeTransport({SEARCH: [CHALLENGE, FakeResponse(200, search_html([1], None))]})
    f = t.fetcher(on_blocked=lambda kind, status: blocked_kinds.append((kind, status)))
    res = run(f.fetch(SEARCH))
    assert res.attempts == 2 and res.profile == "chrome124" and res.http_version == "http1.1"
    assert blocked_kinds == [("challenge", 403)]
    assert f.stats.blocked == 1 and f.stats.rotations == 1
    assert len(t.sessions) == 2 and t.sessions[0].closed and not t.sessions[1].closed  # fresh cookie jar
    assert len(set(t.proxy_sessions)) == 2  # fresh Apify proxy session id
    assert t.sessions[0].profile == "safari18_0" and t.sessions[1].profile == "chrome124"
    assert len(t.sleeps) == 1 and 0.5 <= t.sleeps[0] <= 1.5  # politeness delay only, no backoff for a 403


def test_blocked_on_every_attempt_gives_up_with_blocked_page():
    blocked: list[str] = []
    t = FakeTransport({SEARCH: [CHALLENGE, BLOCKED, CHALLENGE]})
    f = t.fetcher(max_retries=2, on_blocked=lambda kind, status: blocked.append(kind))
    with pytest.raises(BlockedPage) as ei:
        run(f.fetch(SEARCH))
    assert blocked == ["challenge", "blocked", "challenge"]
    assert ei.value.attempts == 3 and ei.value.kind == "challenge" and ei.value.status == 403
    assert [s.profile for s in t.sessions] == ["safari18_0", "chrome124", "chrome120"]
    assert all(s.closed for s in t.sessions) and f.stats.rotations == 2
    assert len(set(t.proxy_sessions)) == 3


def test_ladder_wraps_around_when_more_retries_than_profiles():
    t = FakeTransport({SEARCH: [BLOCKED]})
    f = t.fetcher(max_retries=5)
    with pytest.raises(BlockedPage):
        run(f.fetch(SEARCH))
    assert [s.profile for s in t.sessions] == ["safari18_0", "chrome124", "chrome120", "safari15_5", "safari18_0", "chrome124"]


def test_rate_limit_backs_off_then_rotates():
    t = FakeTransport({SEARCH: [RATE_LIMITED, FakeResponse(200, search_html([1], None))]})
    f = t.fetcher()
    res = run(f.fetch(SEARCH))
    assert res.attempts == 2
    assert 2.0 in t.sleeps  # exponential backoff (2s on the first 429) on top of the politeness delay


def test_transport_error_rotates_and_eventually_raises_fetch_error():
    t = FakeTransport({SEARCH: [ConnectionError("curl: (56) proxy reset")]})
    f = t.fetcher(max_retries=1)
    with pytest.raises(FetchError):
        run(f.fetch(SEARCH))
    assert f.stats.transport_errors == 2 and f.stats.blocked == 0 and len(t.sessions) == 2


def test_referer_is_sent_for_follow_up_pages():
    t = FakeTransport({PAGE2: [FakeResponse(200, search_html([3], None))]})
    f = t.fetcher()
    run(f.fetch(PAGE2, referer=SEARCH))
    _, headers = t.sessions[0].calls[0]
    assert headers["Referer"] == SEARCH and headers["Sec-Fetch-Site"] == "same-origin"


# --------------------------------------------------------------------------- #
# Crawl loop over the HTTP path
# --------------------------------------------------------------------------- #
class Sink:
    def __init__(self):
        self.rows: list[dict] = []

    async def push(self, data):
        self.rows.extend(data if isinstance(data, list) else [data])


def test_run_http_paginates_dedupes_and_caps_at_max_items():
    t = FakeTransport({
        SEARCH: [FakeResponse(200, search_html(list(range(1, 25)), PAGE2, number_found=40))],
        PAGE2: [FakeResponse(200, search_html(list(range(20, 41)), None, number_found=40))],  # 20-24 overlap page 1
    })
    cfg = Config(start_urls=[SEARCH], max_items=30)
    state, sink = RunState(max_items=30), Sink()
    assert run(run_http(cfg, state, t.fetcher(), sink.push, LOG)) is None
    ids = [r["id"] for r in sink.rows]
    assert len(ids) == 30 and len(set(ids)) == 30 and ids[:24] == [str(i) for i in range(1, 25)] and ids[24] == "25"
    assert state.pushed == 30 and state.pages_ok == 2 and state.reached and state.http_pages == 2
    assert [c[0] for c in t.calls] == [SEARCH, PAGE2]
    assert t.sessions[0].calls[1][1]["Referer"] == SEARCH  # page 2 carries page 1 as referer, same cookie jar
    assert evaluate_run(state) is None


def test_run_http_stops_paginating_once_reached():
    t = FakeTransport({SEARCH: [FakeResponse(200, search_html(list(range(1, 25)), PAGE2))]})
    state, sink = RunState(max_items=10), Sink()
    run(run_http(Config(start_urls=[SEARCH], max_items=10), state, t.fetcher(), sink.push, LOG))
    assert len(sink.rows) == 10 and [c[0] for c in t.calls] == [SEARCH]


def test_run_http_include_description_fetches_listing_pages_and_keeps_card_on_block():
    ok_listing = "https://www.gumtree.com.au/web/listing/components/1"
    bad_listing = "https://www.gumtree.com.au/web/listing/components/2"
    t = FakeTransport({
        SEARCH: [FakeResponse(200, search_html([1, 2], None))],
        ok_listing: [FakeResponse(200, LISTING_HTML)],
        bad_listing: [BLOCKED],
    })
    cfg = Config(start_urls=[SEARCH], max_items=10, include_description=True)
    state, sink = RunState(max_items=10), Sink()
    assert run(run_http(cfg, state, t.fetcher(max_retries=1), sink.push, LOG)) is None  # http mode: no handover
    by_id = {r["id"]: r for r in sink.rows}
    assert by_id["1"]["description"].startswith("ASUS TUF Gaming RTX 4070 Ti (OC Edition)")
    assert by_id["1"]["condition"] == "Used" and by_id["1"]["postcode"] == "2158" and by_id["1"]["sellerType"] == "private"
    assert by_id["2"]["description"] is None and by_id["2"]["descriptionError"].startswith("BlockedPage")
    assert state.pushed == 2 and state.failed_requests == 1 and state.blocked_hits == 0  # on_blocked not wired here
    assert t.sessions[0].calls[1][1]["Referer"] == SEARCH  # listing fetched with the search page as referer


def test_run_http_auto_mode_hands_over_to_browser_after_two_giveups():
    u1, u2, u3 = SEARCH, "https://www.gumtree.com.au/s-components/c18552", "https://www.gumtree.com.au/s-monitors/c20049"
    t = FakeTransport({u1: [CHALLENGE], u2: [BLOCKED], u3: [FakeResponse(200, search_html([9], None))]})
    state, sink = RunState(max_items=10), Sink()
    f = t.fetcher(max_retries=0, on_blocked=lambda k, s: setattr(state, "blocked_hits", state.blocked_hits + 1))
    pending = run(run_http(Config(start_urls=[u1, u2, u3], max_items=10), state, f, sink.push, LOG, handover_after_giveups=2))
    assert isinstance(pending, PendingWork) and pending
    assert pending.searches == [(u2, 1), (u3, 1)] and pending.listings == []  # u2 re-queued, u3 never tried
    assert state.failed_requests == 2 and state.blocked_hits == 2 and sink.rows == []
    assert [c[0] for c in t.calls] == [u1, u2]


def test_run_http_only_mode_keeps_going_after_blocks():
    u1, u2, u3 = SEARCH, "https://www.gumtree.com.au/s-components/c18552", "https://www.gumtree.com.au/s-monitors/c20049"
    t = FakeTransport({u1: [CHALLENGE], u2: [BLOCKED], u3: [FakeResponse(200, search_html([9], None))]})
    state, sink = RunState(max_items=10), Sink()
    pending = run(run_http(Config(start_urls=[u1, u2, u3], max_items=10), state, t.fetcher(max_retries=0), sink.push, LOG))
    assert pending is None and [r["id"] for r in sink.rows] == ["9"] and state.failed_requests == 2
    assert evaluate_run(state) is None  # something was scraped


def test_run_http_handover_during_listing_fetches_returns_remaining_listings():
    l1 = "https://www.gumtree.com.au/web/listing/components/1"
    l2 = "https://www.gumtree.com.au/web/listing/components/2"
    l3 = "https://www.gumtree.com.au/web/listing/components/3"
    t = FakeTransport({SEARCH: [FakeResponse(200, search_html([1, 2, 3], None))], l1: [BLOCKED], l2: [BLOCKED], l3: [FakeResponse(200, LISTING_HTML)]})
    cfg = Config(start_urls=[SEARCH], max_items=10, include_description=True)
    state, sink = RunState(max_items=10), Sink()
    pending = run(run_http(cfg, state, t.fetcher(max_retries=0), sink.push, LOG, handover_after_giveups=2))
    assert pending is not None and pending.searches == [] and [it["id"] for it in pending.listings] == ["2", "3"]
    assert [r["id"] for r in sink.rows] == ["1"] and sink.rows[0]["descriptionError"].startswith("BlockedPage")


def test_run_http_zero_results_and_selector_failure_are_counted():
    zero = "https://www.gumtree.com.au/s-zzqqxxyyzz/k0"
    t = FakeTransport({zero: [FakeResponse(200, ZERO_HTML)], SEARCH: [FakeResponse(200, SHELL_HTML)]})
    state, sink = RunState(max_items=10), Sink()
    assert run(run_http(Config(start_urls=[zero], max_items=10), state, t.fetcher(), sink.push, LOG)) is None
    assert state.zero_result_pages == 1 and evaluate_run(state) is None
    state2 = RunState(max_items=10)
    run(run_http(Config(start_urls=[SEARCH], max_items=10), state2, t.fetcher(), sink.push, LOG))
    assert state2.empty_pages == 1 and "no listing matched the selectors" in evaluate_run(state2)


def test_run_http_all_blocked_fails_the_run():
    t = FakeTransport({SEARCH: [CHALLENGE]})
    state, sink = RunState(max_items=10), Sink()
    f = t.fetcher(max_retries=1, on_blocked=lambda k, s: setattr(state, "blocked_hits", state.blocked_hits + 1))
    run(run_http(Config(start_urls=[SEARCH], max_items=10), state, f, sink.push, LOG))
    msg = evaluate_run(state)
    assert msg and "2 blocked response(s)" in msg and "1 request(s) failed" in msg


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def test_fetch_mode_input_defaults_to_auto_and_is_validated():
    base = {"searchUrls": [{"url": SEARCH}]}
    assert config_from_input(base).fetch_mode == "auto"
    assert config_from_input(base).max_items == 24  # cheap first run
    assert config_from_input({**base, "fetchMode": "HTTP"}).fetch_mode == "http"
    assert config_from_input({**base, "fetchMode": "browser"}).fetch_mode == "browser"
    with pytest.raises(ValueError):
        config_from_input({**base, "fetchMode": "curl"})
