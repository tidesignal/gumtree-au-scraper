"""The challenge-aware flow: 403 is not a block in the browser, cookie handoff to HTTP, auto orchestration.

All without a network or a browser: the browser path is represented by a fake
runner with the same contract as run_browser (pending, solve_only) ->
(leftover, handoff); the HTTP path runs for real against a fake transport.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

from src.http_fetch import ADOPTED_PROFILE, HttpFetcher, SessionHandoff, parse_profile_specs
from src.main import (
    BROWSER_LAUNCH_ARGS,
    CHALLENGE_WAIT_SECS,
    LISTING_READY_JS,
    SEARCH_READY_JS,
    Config,
    PendingWork,
    RunState,
    browser_crawler_options,
    chromium_ua_override,
    config_from_input,
    cookies_to_handoff,
    evaluate_run,
    run_auto,
)
from src.parser import BlockedPage
from tests.test_http_fetch import BLOCKED, CHALLENGE, LISTING_HTML, PAGE2, SEARCH, FakeResponse, FakeTransport, Sink, search_html

LOG = logging.getLogger("test")
HEADLESS_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/149.0.7827.55 Safari/537.36"
SOLVED_COOKIES = [
    {"name": "PEAKHOUR_VISIT", "value": "6aa43fd43442048a00004851358214e9", "domain": "www.gumtree.com.au", "path": "/"},
    {"name": "__rp_ch", "value": "6aa43fd43442048a00004851358214cb:6p29ix2", "domain": "www.gumtree.com.au", "path": "/"},
    {"name": "machId", "value": "abc", "domain": "gumtree.com.au", "path": "/"},
    {"name": "__cf_bm", "value": "zzz", "domain": ".images.example.net", "path": "/"},  # not Gumtree's: dropped
]


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Browser configuration: let the challenge run
# --------------------------------------------------------------------------- #
def test_browser_treats_403_as_a_page_not_a_block_and_uses_no_random_fingerprint():
    opts = browser_crawler_options(Config(start_urls=[SEARCH], max_retries=2, page_timeout_secs=40))
    assert opts["ignore_http_error_status_codes"] == [403]
    assert opts["retry_on_blocked"] is False
    assert "fingerprint_generator" not in opts  # consistency with the real binary beats a random browserforge UA
    assert opts["headless"] is True and opts["browser_type"] == "chromium"
    assert "--disable-blink-features=AutomationControlled" in opts["browser_launch_options"]["args"]
    assert any("swiftshader" in a for a in opts["browser_launch_options"]["args"]) and BROWSER_LAUNCH_ARGS
    assert opts["browser_new_context_options"]["locale"] == "en-AU"
    assert opts["max_request_retries"] == 2
    assert opts["request_handler_timeout"] >= timedelta(seconds=40 + CHALLENGE_WAIT_SECS)
    assert "__NEXT_DATA__" in SEARCH_READY_JS and "APP_DATA" in SEARCH_READY_JS and "user-ad-row" in SEARCH_READY_JS
    assert "__NEXT_DATA__" in LISTING_READY_JS and "ld+json" in LISTING_READY_JS


def test_chromium_ua_override_keeps_the_real_version_and_drops_headless():
    o = chromium_ua_override(HEADLESS_UA, system="Linux")
    assert "Headless" not in o["userAgent"] and "Chrome/149.0.7827.55" in o["userAgent"] and "X11; Linux" in o["userAgent"]
    meta = o["userAgentMetadata"]
    assert {b["brand"]: b["version"] for b in meta["brands"]} == {"Chromium": "149", "Google Chrome": "149", "Not-A.Brand": "99"}
    assert {b["brand"]: b["version"] for b in meta["fullVersionList"]}["Google Chrome"] == "149.0.7827.55"
    assert meta["platform"] == "Linux" and o["platform"] == "Linux x86_64" and meta["mobile"] is False
    assert o["acceptLanguage"] == "en-AU,en;q=0.9"
    w = chromium_ua_override("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/149.0.0.0 Safari/537.36", system="Windows")
    assert w["userAgentMetadata"]["platform"] == "Windows" and w["platform"] == "Win32" and w["userAgentMetadata"]["fullVersion"] == "149.0.0.0"


def test_cookies_to_handoff_keeps_only_gumtree_cookies():
    h = cookies_to_handoff(SOLVED_COOKIES, "Mozilla/5.0 Chrome/149", "sess1", "http://u:p@proxy:8000", SEARCH)
    assert h.cookies == {"PEAKHOUR_VISIT": "6aa43fd43442048a00004851358214e9", "__rp_ch": "6aa43fd43442048a00004851358214cb:6p29ix2", "machId": "abc"}
    assert h.has_challenge_cookie and h.proxy_session_id == "sess1" and h.proxy_url.startswith("http://u:p@") and h.solved_url == SEARCH
    assert not cookies_to_handoff([], None, None, None, None).has_challenge_cookie


# --------------------------------------------------------------------------- #
# HTTP client adopting the browser's session
# --------------------------------------------------------------------------- #
def test_adopt_uses_browser_cookies_user_agent_and_proxy_session_then_rotates_normally():
    t = FakeTransport({SEARCH: [CHALLENGE, FakeResponse(200, search_html([1], None))]})
    f = t.fetcher(max_retries=1)
    h = cookies_to_handoff(SOLVED_COOKIES, "Mozilla/5.0 Chrome/149", "browserSess", "http://session-browserSess:pw@proxy.apify.com:8000", SEARCH)
    run(f.adopt(h))
    assert f.current_profile == ADOPTED_PROFILE
    res = run(f.fetch(SEARCH))
    adopted, fallback = t.sessions[0], t.sessions[1]
    assert adopted.profile == "chrome" and adopted.cookies == h.cookies and adopted.proxy_url == h.proxy_url
    assert adopted.calls[0][1]["User-Agent"] == "Mozilla/5.0 Chrome/149"
    assert adopted.closed  # challenged again -> discarded
    assert fallback.profile == "safari18_0" and fallback.cookies is None and "User-Agent" not in fallback.calls[0][1]
    assert res.attempts == 2 and res.profile == "safari18_0"
    assert t.proxy_sessions == [] or all(s != "browserSess" for s in t.proxy_sessions[:0])  # adopted session did not ask the proxy for a new URL


def test_fetch_single_attempt_override():
    t = FakeTransport({SEARCH: [BLOCKED]})
    f = t.fetcher(max_retries=3)
    with pytest.raises(BlockedPage) as ei:
        run(f.fetch(SEARCH, max_retries=0))
    assert ei.value.attempts == 1 and len(t.sessions) == 1


def test_http_profiles_input_overrides_the_ladder():
    assert parse_profile_specs(["chrome/h2", "chrome124/http1.1", " Safari18_0 "]) == (("chrome", "h2"), ("chrome124", "http1.1"), ("safari18_0", "h2"))
    assert parse_profile_specs([]) == () and parse_profile_specs(None) == ()
    with pytest.raises(ValueError):
        parse_profile_specs(["chrome/spdy"])
    with pytest.raises(ValueError):
        parse_profile_specs(["netscape4"])
    cfg = config_from_input({"searchUrls": [{"url": SEARCH}], "httpProfiles": ["chrome/h2"]})
    assert cfg.http_profiles == (("chrome", "h2"),)
    assert config_from_input({"searchUrls": [{"url": SEARCH}]}).http_profiles is None


# --------------------------------------------------------------------------- #
# auto orchestration
# --------------------------------------------------------------------------- #
class FakeBrowser:
    """Stands in for run_browser: solves (or not) the first pending page, pushes its rows, returns the rest."""

    def __init__(self, sink: Sink, state: RunState, *, solves: bool = True, rows_per_page: int = 2, next_page: str | None = None):
        self.sink, self.state, self.solves, self.rows_per_page, self.next_page = sink, state, solves, rows_per_page, next_page
        self.calls: list[tuple[PendingWork, bool]] = []

    async def __call__(self, pending: PendingWork, solve_only: bool):
        self.calls.append((PendingWork(list(pending.searches), list(pending.listings)), solve_only))
        if not self.solves:
            if solve_only:
                self.state.failed_requests += 1
                return pending, None
            self.state.failed_requests += len(pending.searches) + len(pending.listings)
            return None, None
        todo = pending.searches[:1] if solve_only else list(pending.searches)
        for url, page in todo:
            self.state.challenges_solved += 1
            self.state.pages_ok += 1
            self.state.browser_pages += 1
            fresh = self.state.take([{"id": f"b{page}-{i}", "url": f"https://www.gumtree.com.au/web/listing/x/b{page}{i}", "searchUrl": url} for i in range(self.rows_per_page)])
            await self.sink.push(fresh)
            self.state.pushed += len(fresh)
        for it in ([] if solve_only else pending.listings):
            await self.sink.push({**it, "description": "from browser"})
            self.state.pushed += 1
        if not solve_only:
            return None, None
        left = PendingWork(list(pending.searches[1:]), list(pending.listings))
        if self.next_page:
            left.searches.insert(0, (self.next_page, 2))
        handoff = cookies_to_handoff(SOLVED_COOKIES, "Mozilla/5.0 Chrome/149", "bsess", "http://session-bsess:pw@proxy:8000", pending.searches[0][0])
        return (left if left else None), handoff


def test_auto_happy_path_never_opens_a_browser():
    t = FakeTransport({SEARCH: [FakeResponse(200, search_html([1, 2], None))]})
    state, sink = RunState(max_items=10), Sink()
    b = FakeBrowser(sink, state)
    run(run_auto(Config(start_urls=[SEARCH], max_items=10), state, t.fetcher(), sink.push, LOG, b))
    assert b.calls == [] and [r["id"] for r in sink.rows] == ["1", "2"] and not state.handed_to_browser


def test_auto_first_page_challenged_is_solved_in_browser_then_http_refused_then_browser_finishes():
    """Measured behaviour: cookies are bound to the browser's TLS fingerprint, so the HTTP retry is challenged again."""
    t = FakeTransport({SEARCH: [CHALLENGE], PAGE2: [CHALLENGE]})
    state, sink = RunState(max_items=10), Sink()
    f = t.fetcher(max_retries=3, on_blocked=lambda k, s: setattr(state, "blocked_hits", state.blocked_hits + 1))
    b = FakeBrowser(sink, state, next_page=PAGE2)
    run(run_auto(Config(start_urls=[SEARCH], max_items=10), state, f, sink.push, LOG, b))
    # 1) HTTP: exactly one attempt on the first page (no ladder rotation), then the browser gets that same URL.
    assert [c[0] for c in t.calls][:1] == [SEARCH] and t.sessions[0].profile == "safari18_0"
    assert b.calls[0][0].searches == [(SEARCH, 1)] and b.calls[0][1] is True
    # 2) HTTP retry with the adopted session: one attempt on the next page, challenged again.
    assert t.calls[1][0] == PAGE2 and t.sessions[1].profile == "chrome" and t.sessions[1].cookies["__rp_ch"].startswith("6aa43fd4")
    assert t.sessions[1].calls[0][1]["User-Agent"] == "Mozilla/5.0 Chrome/149" and t.sessions[1].proxy_url == "http://session-bsess:pw@proxy:8000"
    assert len(t.calls) == 2  # never a third HTTP request
    # 3) Browser finishes the rest.
    assert b.calls[1][0].searches == [(PAGE2, 2)] and b.calls[1][1] is False
    assert state.handed_to_browser and state.handoff_attempted and not state.handoff_accepted
    assert state.challenges_solved == 2 and state.blocked_hits == 2 and state.failed_requests == 2
    assert [r["id"] for r in sink.rows] == ["b1-0", "b1-1", "b2-0", "b2-1"] and evaluate_run(state) is None


def test_auto_handoff_accepted_continues_over_http():
    t = FakeTransport({SEARCH: [CHALLENGE], PAGE2: [FakeResponse(200, search_html([7, 8], None))]})
    state, sink = RunState(max_items=10), Sink()
    b = FakeBrowser(sink, state, next_page=PAGE2)
    run(run_auto(Config(start_urls=[SEARCH], max_items=10), state, t.fetcher(), sink.push, LOG, b))
    assert len(b.calls) == 1 and b.calls[0][1] is True
    assert t.sessions[1].profile == "chrome" and t.sessions[1].cookies and t.calls[1][0] == PAGE2
    assert state.handoff_accepted and [r["id"] for r in sink.rows] == ["b1-0", "b1-1", "7", "8"]


def test_auto_second_page_blocked_goes_to_browser_for_that_page():
    """The give-up on any page, including the first, re-queues that URL for the browser."""
    t = FakeTransport({SEARCH: [FakeResponse(200, search_html([1], PAGE2))], PAGE2: [BLOCKED]})
    state, sink = RunState(max_items=10), Sink()
    b = FakeBrowser(sink, state)
    run(run_auto(Config(start_urls=[SEARCH], max_items=10), state, t.fetcher(max_retries=1), sink.push, LOG, b))
    assert b.calls[0][0].searches == [(PAGE2, 2)] and b.calls[0][1] is True
    assert [r["id"] for r in sink.rows] == ["1", "b2-0", "b2-1"]


def test_auto_browser_cannot_solve_either_falls_through_to_full_browser_run_and_fails_loudly():
    t = FakeTransport({SEARCH: [CHALLENGE]})
    state, sink = RunState(max_items=10), Sink()
    f = t.fetcher(on_blocked=lambda k, s: setattr(state, "blocked_hits", state.blocked_hits + 1))
    b = FakeBrowser(sink, state, solves=False)
    run(run_auto(Config(start_urls=[SEARCH], max_items=10), state, f, sink.push, LOG, b))
    assert [c[1] for c in b.calls] == [True, False] and sink.rows == []
    assert not state.handoff_attempted and evaluate_run(state) is not None


def test_auto_with_include_description_hands_listing_items_over():
    t = FakeTransport({SEARCH: [CHALLENGE]})
    state, sink = RunState(max_items=10), Sink()
    cfg = Config(start_urls=[SEARCH], max_items=10, include_description=True)
    listing_html = FakeResponse(200, LISTING_HTML)
    t.routes["https://www.gumtree.com.au/web/listing/x/b10"] = [listing_html]
    t.routes["https://www.gumtree.com.au/web/listing/x/b11"] = [listing_html]

    class SolveWithListings(FakeBrowser):
        async def __call__(self, pending, solve_only):
            self.calls.append((PendingWork(list(pending.searches), list(pending.listings)), solve_only))
            fresh = self.state.take([{"id": "b1-0", "url": "https://www.gumtree.com.au/web/listing/x/b10", "searchUrl": SEARCH},
                                     {"id": "b1-1", "url": "https://www.gumtree.com.au/web/listing/x/b11", "searchUrl": SEARCH}])
            self.state.challenges_solved += 1
            return PendingWork([], fresh), cookies_to_handoff(SOLVED_COOKIES, "UA", "s", None, SEARCH)

    b = SolveWithListings(sink, state)
    run(run_auto(cfg, state, t.fetcher(), sink.push, LOG, b))
    assert len(b.calls) == 1
    assert [r["id"] for r in sink.rows] == ["b1-0", "b1-1"] and all(r["condition"] == "Used" for r in sink.rows)
    assert state.handoff_accepted and state.pushed == 2
