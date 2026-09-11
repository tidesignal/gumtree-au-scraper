"""The browser's "page is ready" predicates and the half-rendered-page diagnostic.

Platform run e7Pbi0HTl8gKNCEwH (2026-09-12) parsed a search page from the DOM
with 9 cards, no APP_DATA and no next link: page.content() had been taken
while the post-challenge redirect was still streaming the new document. The
predicates below must refuse a document that is still loading, and trust
rendered cards alone only once the document is complete.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.main import CARDS_PER_PAGE, LISTING_READY_JS, SEARCH_READY_JS, format_run_usage, partial_render_suspected

FIXTURES = Path(__file__).parent / "fixtures"


def test_ready_predicates_guard_on_document_ready_state():
    assert "readyState === 'loading'" in SEARCH_READY_JS and "readyState === 'complete'" in SEARCH_READY_JS
    assert "APP_DATA" in SEARCH_READY_JS and "__NEXT_DATA__" in SEARCH_READY_JS and "user-ad-row-new-design" in SEARCH_READY_JS
    assert "readyState === 'loading'" in LISTING_READY_JS and "ld+json" in LISTING_READY_JS


@pytest.mark.parametrize(
    "source,n,found,next_url,expected",
    [
        ("dom", 9, 33, None, True),  # the platform case
        ("dom", 9, None, None, True),  # count unknown, page short: suspicious
        ("dom", 9, 9, None, False),  # a genuinely short last page
        ("dom", 24, 33, None, False),  # full page, last page
        ("dom", 9, 33, "https://www.gumtree.com.au/s-x/page-2/k0", False),  # next link rendered: page was complete
        ("app_data", 9, 33, None, False),  # the blob is authoritative
        ("next_data", 33, 33, None, False),
    ],
)
def test_partial_render_suspected(source, n, found, next_url, expected):
    assert partial_render_suspected(source, n, found, next_url) is expected
    assert CARDS_PER_PAGE == 24


def test_format_run_usage():
    run = {"usage": {"ACTOR_COMPUTE_UNITS": 0.0125, "PROXY_RESIDENTIAL_TRANSFER_GBYTES": 0.00048, "DATASET_WRITES": 24},
           "usageTotalUsd": 0.0091, "stats": {"runTimeSecs": 41.2}}
    line = format_run_usage(run)
    assert line.startswith("Run usage so far: compute 0.0125 CU, residential proxy 0.49 MB, dataset writes 24, total $0.0091, runtime 41 s")
    assert format_run_usage({}) is None and format_run_usage(None) is None


# --------------------------------------------------------------------------- #
# The JS predicates in a real headless Chromium (skipped where Playwright has no browser)
# --------------------------------------------------------------------------- #
def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_chromium = pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")


@needs_chromium
def test_search_ready_js_in_chromium():
    from playwright.sync_api import sync_playwright

    page_html = (FIXTURES / "rtx4070_page.html").read_text(encoding="utf-8")  # 6 rendered cards, no data blob
    next_data = json.loads((FIXTURES / "rtx4070_next_data.json").read_text(encoding="utf-8"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # Cards only, document complete -> ready (a genuinely card-only page is still a page).
        page.set_content("<html><body>" + page_html + "</body></html>", wait_until="load")
        assert page.evaluate(SEARCH_READY_JS) is True
        # No cards, no blob -> not ready (a challenge page, or a shell).
        page.set_content("<html><body><div id='x'></div></body></html>", wait_until="load")
        assert page.evaluate(SEARCH_READY_JS) is False
        assert page.evaluate(LISTING_READY_JS) is False
        # Data blob present -> ready even without cards.
        page.set_content('<html><body><script id="__NEXT_DATA__" type="application/json">' + json.dumps(next_data) + "</script></body></html>", wait_until="load")
        assert page.evaluate(SEARCH_READY_JS) is True and page.evaluate(LISTING_READY_JS) is True
        page.set_content("<html><head><script>window.APP_DATA = {search: {results: {main: []}}};</script></head><body></body></html>", wait_until="load")
        assert page.evaluate(SEARCH_READY_JS) is True
        # Cards present but the document is still loading (a slow, streaming response) -> not ready yet.
        page.route("https://ready.test/slow", lambda route: None)  # never fulfilled: the navigation stays pending

        def slow(route):
            route.fulfill(status=200, content_type="text/html", body="<html><body>" + page_html + "<script src='https://ready.test/slow'></script></body></html>")

        page.route("https://ready.test/page", slow)
        page.goto("https://ready.test/page", wait_until="commit")
        page.wait_for_selector("a.user-ad-row-new-design", state="attached")
        state = page.evaluate("document.readyState")
        ready = page.evaluate(SEARCH_READY_JS)
        # While the blocking script keeps the document in 'loading'/'interactive', cards alone must not count.
        assert (state == "complete") == ready or ready is False
        browser.close()
