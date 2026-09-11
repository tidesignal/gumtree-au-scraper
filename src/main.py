"""Gumtree Australia Scraper - Apify Actor entry point.

Fetch layer : two paths, chosen by the ``fetchMode`` input.
              * ``http`` (src/http_fetch.py): curl_cffi impersonating a real
                browser's TLS/HTTP-2 fingerprint. No Chromium. Cheapest, and
                what passes gumtree.com.au's bot mitigation (Peakhour) from a
                connection Peakhour trusts.
              * ``browser``: Crawlee PlaywrightCrawler (headless Chromium).
                The only client that can *answer* Peakhour's JavaScript
                challenge, which Peakhour demands from proxy IPs whatever the
                TLS profile. 403 is not treated as an error here: the challenge
                page is allowed to run (proof-of-work + browser fingerprint,
                POSTed back), and the page is parsed once the real content has
                replaced it. Measured 2026-09-12: 0.6-1.0 s per challenge in
                headless Chromium, provided the client hints do not say
                "HeadlessChrome" (see chromium_ua_override).
              * ``auto`` (default): one HTTP attempt; on a block the browser
                solves the challenge for that same page; its cookies are then
                offered to the HTTP client once (they are bound to the
                browser's TLS fingerprint and were rejected in every local
                test, but the check costs one request); if that is refused
                the rest of the run stays in the browser.
Parse layer : src/parser.py - pure functions, unit-tested on saved real pages.

Failure policy (deliberate)
---------------------------
A run that ends with zero listings exits non-zero unless every page it loaded
was a genuine "no results" page. A silently empty dataset looks exactly like
"every listing sold" to any downstream diffing job, and that is worse than a
failed run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import random
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus, urlencode, urlparse

from apify import Actor

from .http_fetch import FetchError, HttpFetcher, SessionHandoff, parse_profile_specs
from .parser import (
    BASE_URL,
    BlockedPage,
    SelectorsMatchedNothing,
    looks_blocked,
    looks_like_gumtree,
    parse_listing_page,
    parse_search_page,
)

LABEL_SEARCH = "SEARCH"
LABEL_LISTING = "LISTING"

FETCH_MODES = ("auto", "http", "browser")

# Location IDs verified on gumtree.com.au (2026-09-11). Anything else must be
# passed as the numeric id from a Gumtree URL.
LOCATION_ALIASES = {"sydney": "3003435", "sydney region": "3003435"}

# How long the browser waits for the Peakhour challenge to resolve into real
# content. Measured 0.6-1.0 s locally; the budget covers a slow proxy.
CHALLENGE_WAIT_SECS = 20

# "The real page is here" for a search page. The document must be fully parsed
# (readyState past 'loading'): after the challenge's 307 -> 200 redirect the new
# page streams in, and the first listing cards exist long before the data blob
# (window.APP_DATA sits at ~58% of the document, the pagination link at ~54%).
# Platform run e7Pbi0HTl8gKNCEwH snapshotted such a half-parsed page: 9 cards,
# no APP_DATA, no next link. Cards alone are trusted only once readyState is
# 'complete'.
SEARCH_READY_JS = """() => {
  if (document.readyState === 'loading') return false;
  const blob = (window.APP_DATA && window.APP_DATA.search && window.APP_DATA.search.results)
    || document.getElementById('__NEXT_DATA__');
  if (blob) return true;
  return document.readyState === 'complete' && !!document.querySelector('a.user-ad-row-new-design');
}"""
# ... and for a listing page (a Next.js app with JSON-LD).
LISTING_READY_JS = """() => {
  if (document.readyState === 'loading') return false;
  return !!(document.getElementById('__NEXT_DATA__') || document.querySelector('script[type="application/ld+json"]'));
}"""
# Gumtree's classic result page holds this many cards; fewer from the DOM path
# with more results on the site and no next link means a half-rendered page.
CARDS_PER_PAGE = 24
APP_DATA_SEARCH_JS = "() => (window.APP_DATA && window.APP_DATA.search) ? ({ search: window.APP_DATA.search }) : null"

# Chromium flags for the browser path. AutomationControlled off keeps
# navigator.webdriver false; the ANGLE/SwiftShader flags give the challenge a
# WebGL context on a machine without a GPU (the Apify platform).
BROWSER_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]
BROWSER_VIEWPORT = {"width": 1366, "height": 768}
# Request URL patterns the browser does not load (Crawlee block_requests): page weight, not page data.
BLOCKED_RESOURCE_PATTERNS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".webm", ".pdf")


# --------------------------------------------------------------------------- #
# Input handling
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    start_urls: list[str]
    max_items: int = 24
    include_description: bool = False
    fetch_mode: str = "auto"
    http_profiles: tuple[tuple[str, str], ...] | None = None  # None = built-in ladder
    max_retries: int = 3
    delay_secs: float = 2.0
    page_timeout_secs: int = 60


def _digits(value) -> str | None:
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    return m.group(0) if m else None


def resolve_location(value) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    key = str(value).strip().lower()
    if key in LOCATION_ALIASES:
        return LOCATION_ALIASES[key]
    digits = _digits(value)
    if digits and digits == key:
        return digits
    raise ValueError(
        f"Unknown location {value!r}. Use the numeric Gumtree location id (the number after 'l' "
        f"in any gumtree.com.au result URL, e.g. 3003435 for Sydney Region) or one of: "
        f"{', '.join(sorted(LOCATION_ALIASES))}."
    )


def build_search_url(
    keyword: str | None,
    location: str | None = None,
    category: str | None = None,
    ad_type: str = "all",
    sort_by: str = "rank",
) -> str:
    """Build a gumtree.com.au search URL from parts.

    URL grammar verified 2026-09-11 (Gumtree rewrites the slug segments to its
    own canonical names, so placeholder slugs are fine):
        /s-{kw}/k0                        keyword
        /s-all/c{cat}                     category
        /s-all/l{loc}                     location
        /s-all/{kw}/k0c{cat}              keyword + category
        /s-all/{kw}/k0l{loc}              keyword + location
        /s-all/all/c{cat}l{loc}           category + location
        /s-all/all/{kw}/k0c{cat}l{loc}    all three
    Query: ?sort=rank|date|price_asc|price_desc  &ad=offering|wanted
    """
    kw = (keyword or "").strip()
    loc = resolve_location(location)
    cat = _digits(category)
    if not (kw or loc or cat):
        raise ValueError("Provide searchUrls, or at least one of keyword / category / location.")

    segments: list[str] = []
    if kw and not loc and not cat:
        segments.append("s-" + quote_plus(kw))
    else:
        segments.append("s-all")
        if loc and cat:
            segments.append("all")
        if kw:
            segments.append(quote_plus(kw))
    tail = ("k0" if kw else "") + (f"c{cat}" if cat else "") + (f"l{loc}" if loc else "")
    if tail:
        segments.append(tail)

    query: dict[str, str] = {}
    if sort_by and sort_by != "rank":
        query["sort"] = sort_by
    if ad_type in ("offering", "wanted"):
        query["ad"] = ad_type
    url = BASE_URL + "/" + "/".join(segments)
    return url + ("?" + urlencode(query) if query else "")


def _validate_gumtree_url(url: str) -> str:
    url = (url or "").strip()
    host = urlparse(url).netloc.lower()
    # localhost is allowed so the full pipeline can be exercised against saved pages in tests.
    if not (host.endswith("gumtree.com.au") or host.split(":")[0] in ("localhost", "127.0.0.1")):
        raise ValueError(f"Only gumtree.com.au URLs are supported, got: {url!r}")
    return url


def config_from_input(inp: dict) -> Config:
    urls: list[str] = []
    for entry in inp.get("searchUrls") or []:
        url = entry.get("url") if isinstance(entry, dict) else entry
        if url:
            urls.append(_validate_gumtree_url(str(url)))
    if not urls and any(inp.get(k) for k in ("keyword", "location", "category")):
        urls.append(
            build_search_url(
                inp.get("keyword"),
                inp.get("location"),
                inp.get("category"),
                inp.get("adType") or "all",
                inp.get("sortBy") or "rank",
            )
        )
    if not urls:
        raise ValueError("No start URLs: give 'searchUrls' or a 'keyword' (optionally with location/category).")
    fetch_mode = str(inp.get("fetchMode") or "auto").strip().lower()
    if fetch_mode not in FETCH_MODES:
        raise ValueError(f"fetchMode must be one of {FETCH_MODES}, got {fetch_mode!r}")
    profiles = parse_profile_specs(inp.get("httpProfiles")) or None
    return Config(
        start_urls=urls,
        max_items=max(1, int(inp.get("maxItems") or 24)),
        include_description=bool(inp.get("includeDescription", False)),
        fetch_mode=fetch_mode,
        http_profiles=profiles,
        max_retries=max(0, int(inp.get("maxRetries", 3))),
        delay_secs=max(0.0, float(inp.get("requestDelaySecs", 2))),
        page_timeout_secs=max(10, int(inp.get("pageTimeoutSecs", 60))),
    )


# --------------------------------------------------------------------------- #
# Run state and the end-of-run guard
# --------------------------------------------------------------------------- #
@dataclass
class RunState:
    max_items: int
    seen: set[str] = field(default_factory=set)
    reserved: int = 0  # unique items accepted (pushed, or queued for a listing fetch)
    pushed: int = 0
    pages_ok: int = 0
    zero_result_pages: int = 0
    empty_pages: int = 0  # loaded fine, parsed nothing -> selector failure
    blocked_hits: int = 0
    failed_requests: int = 0
    http_pages: int = 0  # result/listing pages fetched over the HTTP path
    browser_pages: int = 0  # ... and over the browser path
    challenges_solved: int = 0  # browser pages that started as a 403 challenge and ended as content
    partial_pages: int = 0  # DOM-parsed pages that look half-rendered (see partial_render_suspected)
    handoff_attempted: bool = False
    handoff_accepted: bool = False
    handed_to_browser: bool = False

    @property
    def reached(self) -> bool:
        return self.reserved >= self.max_items

    def take(self, items: list[dict]) -> list[dict]:
        fresh: list[dict] = []
        for it in items:
            if self.reached:
                break
            if it["id"] in self.seen:
                continue
            self.seen.add(it["id"])
            self.reserved += 1
            fresh.append(it)
        return fresh


def evaluate_run(state: RunState) -> str | None:
    """Return a failure message, or None when the run may finish successfully.

    Zero items is acceptable only when every loaded page was a genuine empty
    search and nothing was blocked or unparseable.
    """
    if state.pushed > 0:
        return None
    if state.zero_result_pages > 0 and state.empty_pages == 0 and state.failed_requests == 0 and state.blocked_hits == 0:
        return None
    reasons = []
    if state.blocked_hits:
        reasons.append(f"{state.blocked_hits} blocked response(s) from bot mitigation")
    if state.empty_pages:
        reasons.append(f"{state.empty_pages} page(s) loaded but no listing matched the selectors")
    if state.failed_requests:
        reasons.append(f"{state.failed_requests} request(s) failed after retries")
    if not reasons:
        reasons.append("no page produced any listing")
    return "0 listings scraped: " + "; ".join(reasons) + ". Refusing to finish successfully with an empty dataset."


# --------------------------------------------------------------------------- #
# Proxy
# --------------------------------------------------------------------------- #
async def make_proxy_configuration(proxy_input: dict | None):
    if not proxy_input:
        Actor.log.warning("No proxy configured - connecting directly.")
        return None
    wants_apify = bool(proxy_input.get("useApifyProxy"))
    if wants_apify and not (Actor.is_at_home() or os.environ.get("APIFY_PROXY_PASSWORD") or os.environ.get("APIFY_TOKEN")):
        Actor.log.warning(
            "Apify Proxy requested but no APIFY_TOKEN / APIFY_PROXY_PASSWORD is set locally - "
            "running WITHOUT proxy. gumtree.com.au accepts direct traffic from residential Australian connections."
        )
        return None
    try:
        return await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
    except Exception as exc:  # noqa: BLE001
        Actor.log.warning(f"Could not create proxy configuration ({exc}); running WITHOUT proxy.")
        return None


# --------------------------------------------------------------------------- #
# Shared: what is left to do when one fetch path stops early
# --------------------------------------------------------------------------- #
@dataclass
class PendingWork:
    searches: list[tuple[str, int]] = field(default_factory=list)  # (url, page number)
    listings: list[dict] = field(default_factory=list)  # card items still waiting for their listing page

    def __bool__(self) -> bool:
        return bool(self.searches or self.listings)


PushFn = Callable[[Any], Awaitable[Any]]
# (pending, solve_only) -> (what is left, session handoff if a challenge was passed)
BrowserRunner = Callable[[PendingWork, bool], Awaitable[tuple[PendingWork | None, SessionHandoff | None]]]


# --------------------------------------------------------------------------- #
# HTTP path (curl_cffi)
# --------------------------------------------------------------------------- #
async def run_http(
    cfg: Config,
    state: RunState,
    fetcher: HttpFetcher,
    push: PushFn,
    log: logging.Logger,
    *,
    pending: PendingWork | None = None,
    handover_after_giveups: int | None = None,
    single_attempt_first: bool = False,
) -> PendingWork | None:
    """Drive the crawl over the HTTP path.

    Returns None when the work is finished. Returns the remaining work (with
    the page that failed re-queued first) once ``handover_after_giveups`` pages
    had to be abandoned because every retry was blocked, so the caller can
    continue in a browser. ``single_attempt_first`` makes the first request a
    probe: one attempt, no ladder rotation.
    """
    pending = pending or PendingWork([(u, 1) for u in cfg.start_urls], [])
    searches: deque[tuple[str, int]] = deque(pending.searches)
    listings: deque[dict] = deque(pending.listings)
    referers: dict[str, str] = {}
    giveups = 0
    probe = single_attempt_first

    def _handover() -> bool:
        return handover_after_giveups is not None and giveups >= handover_after_giveups

    async def _fetch(url: str, referer: str | None):
        nonlocal probe
        try:
            return await fetcher.fetch(url, referer=referer, max_retries=0 if probe else None)
        finally:
            probe = False

    while searches and not state.reached:
        url, page_num = searches.popleft()
        try:
            res = await _fetch(url, referers.get(url))
        except BlockedPage as exc:
            state.failed_requests += 1
            giveups += 1
            log.error(f"Gave up on {url}: {exc}")
            if _handover():
                searches.appendleft((url, page_num))
                return PendingWork(list(searches), list(listings))
            continue
        except FetchError as exc:
            state.failed_requests += 1
            log.error(f"Gave up on {url}: {exc}")
            continue
        state.http_pages += 1

        try:
            items, meta = parse_search_page(res.html, source_url=url, page=page_num, now=datetime.now(timezone.utc))
        except SelectorsMatchedNothing as exc:
            state.empty_pages += 1
            log.error(str(exc))
            continue

        if not items and meta.zero_results:
            state.zero_result_pages += 1
            log.info(f"No results on Gumtree for {url} (genuine empty search).")
            continue

        state.pages_ok += 1
        fresh = state.take(items)
        log.info(
            f"Page {page_num} ({meta.source}, http {res.profile}/{res.http_version}, {res.attempts} attempt(s)) {url}: "
            f"{len(items)} listings, {len(fresh)} new (total found on site: {meta.number_found}); "
            f"accepted so far {state.reserved}/{cfg.max_items}"
        )

        if cfg.include_description:
            for it in fresh:
                if it.get("url"):
                    listings.append(it)
            bare = [it for it in fresh if not it.get("url")]
            if bare:
                await push(bare)
                state.pushed += len(bare)
        elif fresh:
            await push(fresh)
            state.pushed += len(fresh)

        if not state.reached and meta.next_page_url:
            searches.append((meta.next_page_url, page_num + 1))
            referers[meta.next_page_url] = url

    while listings:
        item = dict(listings.popleft())
        url = item["url"]
        error: str | None = None
        try:
            res = await _fetch(url, item.get("searchUrl"))
        except BlockedPage as exc:
            state.failed_requests += 1
            giveups += 1
            log.error(f"Gave up on {url}: {exc}")
            if _handover():
                listings.appendleft(item)
                return PendingWork([], list(listings))
            error = f"{type(exc).__name__}: {exc}"
        except FetchError as exc:
            state.failed_requests += 1
            log.error(f"Gave up on {url}: {exc}")
            error = f"{type(exc).__name__}: {exc}"
        else:
            state.http_pages += 1
            extra = parse_listing_page(res.html)
            if not extra.get("description") and not looks_like_gumtree(res.title, res.html):
                state.empty_pages += 1
                error = f"SelectorsMatchedNothing: Listing page unrecognised: {url}"
                log.error(error)
            else:
                item.update({k: v for k, v in extra.items() if v is not None})
        item.setdefault("description", None)
        if error:
            item["description"] = None
            item["descriptionError"] = error[:300]
        await push(item)
        state.pushed += 1

    return None


# --------------------------------------------------------------------------- #
# Browser path (Playwright / Crawlee)
# --------------------------------------------------------------------------- #
def chromium_ua_override(real_ua: str, system: str | None = None, accept_language: str = "en-AU,en;q=0.9") -> dict:
    """CDP ``Emulation.setUserAgentOverride`` params that make headless Chromium look like the same Chromium, headed.

    Only the "Headless" marker is removed and the client hints (sec-ch-ua*) are
    filled in from the *real* version, so the User-Agent, the client hints and
    the TLS fingerprint all describe one and the same binary. Measured
    2026-09-12: with the stock ``HeadlessChrome`` UA or with only the
    User-Agent string replaced, Peakhour answers ``peakhour-error: blocked``
    (no challenge at all); with the client hints overridden it serves the
    challenge and accepts the answer.
    """
    ua = real_ua.replace("HeadlessChrome/", "Chrome/")
    m = re.search(r"Chrome/((\d+)(?:\.\d+){0,3})", ua)
    full = m.group(1) if m else "0.0.0.0"
    major = m.group(2) if m else "0"
    if full.count(".") < 3:
        full = full + ".0" * (3 - full.count("."))
    plat = {
        "Windows": ("Windows", "15.0.0", "Win32"),
        "Linux": ("Linux", "6.8.0", "Linux x86_64"),
        "Darwin": ("macOS", "14.0.0", "MacIntel"),
    }.get(system or platform.system(), ("Linux", "6.8.0", "Linux x86_64"))
    return {
        "userAgent": ua,
        "acceptLanguage": accept_language,
        "platform": plat[2],
        "userAgentMetadata": {
            "brands": [
                {"brand": "Chromium", "version": major},
                {"brand": "Google Chrome", "version": major},
                {"brand": "Not-A.Brand", "version": "99"},
            ],
            "fullVersionList": [
                {"brand": "Chromium", "version": full},
                {"brand": "Google Chrome", "version": full},
                {"brand": "Not-A.Brand", "version": "99.0.0.0"},
            ],
            "fullVersion": full,
            "platform": plat[0],
            "platformVersion": plat[1],
            "architecture": "x86",
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
    }


def browser_crawler_options(cfg: Config, proxy_configuration=None) -> dict:
    """PlaywrightCrawler kwargs. Kept as data so the two load-bearing choices are testable:

    * ``ignore_http_error_status_codes=[403]`` + ``retry_on_blocked=False``:
      a 403 is the challenge page, not a failure; the handler waits for it to
      resolve instead of Crawlee retiring the session before any JS ran.
    * no ``fingerprint_generator``: a random browserforge fingerprint (UA of
      one browser, TLS of another) is exactly what the challenge cross-checks;
      chromium_ua_override keeps everything consistent with the real binary.
    """
    return {
        "browser_type": "chromium",
        "headless": True,
        "browser_launch_options": {"args": list(BROWSER_LAUNCH_ARGS)},
        "browser_new_context_options": {"locale": "en-AU", "timezone_id": "Australia/Sydney", "viewport": dict(BROWSER_VIEWPORT)},
        "ignore_http_error_status_codes": [403],
        "retry_on_blocked": False,
        "use_session_pool": True,
        "max_request_retries": cfg.max_retries,
        "navigation_timeout": timedelta(seconds=cfg.page_timeout_secs),
        "request_handler_timeout": timedelta(seconds=cfg.page_timeout_secs + CHALLENGE_WAIT_SECS + 30),
        "proxy_configuration": proxy_configuration,
    }


def partial_render_suspected(source: str, n_items: int, number_found, next_page_url) -> bool:
    """True when a DOM-parsed page looks like a snapshot taken before the page finished rendering."""
    if source != "dom" or next_page_url:
        return False
    if n_items >= CARDS_PER_PAGE:
        return False
    return number_found is None or number_found > n_items


def cookies_to_handoff(
    cookies: list[dict], user_agent: str | None, session_id: str | None, proxy_url: str | None, url: str | None
) -> SessionHandoff:
    jar = {
        str(c.get("name")): str(c.get("value"))
        for c in cookies
        if c.get("name") and "gumtree.com.au" in str(c.get("domain") or "")
    }
    return SessionHandoff(cookies=jar, user_agent=user_agent, proxy_session_id=session_id, proxy_url=proxy_url, solved_url=url)


async def run_browser(
    cfg: Config,
    state: RunState,
    proxy_configuration,
    pending: PendingWork,
    *,
    solve_only: bool = False,
) -> tuple[PendingWork | None, SessionHandoff | None]:
    """Crawl ``pending`` in headless Chromium, letting Peakhour's challenge run.

    With ``solve_only`` only the first pending page is fetched (and parsed and
    pushed like any other); the rest, plus whatever that page linked to, is
    returned together with the session that passed the challenge so the
    caller can try to continue over HTTP.
    """
    from crawlee import ConcurrencySettings, Request
    from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
    from crawlee.errors import SessionError

    opts = browser_crawler_options(cfg, proxy_configuration)
    # One page at a time: a solved challenge carries to the next pages of the same session,
    # and two challenges racing in one browser produced retries and timeouts locally.
    opts["concurrency_settings"] = ConcurrencySettings(desired_concurrency=1, max_concurrency=1)
    crawler = PlaywrightCrawler(**opts)

    leftover = PendingWork(list(pending.searches), list(pending.listings))
    handoff: SessionHandoff | None = None
    ua_by_page: dict[int, str] = {}

    @crawler.pre_navigation_hook
    async def prepare(context: PlaywrightCrawlingContext) -> None:
        real_ua = await context.page.evaluate("navigator.userAgent")
        override = chromium_ua_override(real_ua)
        ua_by_page[id(context.page)] = override["userAgent"]
        try:
            cdp = await context.page.context.new_cdp_session(context.page)
            await cdp.send("Emulation.setUserAgentOverride", override)
        except Exception as exc:  # noqa: BLE001 - not Chromium, or CDP unavailable: go on with the stock UA
            Actor.log.warning(f"Could not override client hints ({exc}); the challenge may refuse a HeadlessChrome UA.")
        # The listing page never fires `load` after the challenge redirect (a third-party resource
        # hangs); settle() waits for the real content anyway, so do not wait for `load` here.
        context.goto_options["wait_until"] = "domcontentloaded"
        # Images, fonts and media are most of a page's bytes and none of its data; the challenge
        # needs only scripts. Saves residential-proxy traffic on the platform.
        try:
            await context.block_requests(url_patterns=list(BLOCKED_RESOURCE_PATTERNS))
        except Exception as exc:  # noqa: BLE001 - blocking is an optimisation, never a reason to fail
            Actor.log.debug(f"block_requests unavailable: {exc}")
        if cfg.delay_secs > 0:
            await asyncio.sleep(random.uniform(0.5, 1.5) * cfg.delay_secs)

    async def settle(context: PlaywrightCrawlingContext, ready_js: str) -> tuple[int | None, str, str, bool]:
        """Wait for real content (through the challenge if there is one). Returns status, title, html, solved."""
        status = context.response.status if context.response else None
        headers = {k.lower(): v for k, v in (context.response.headers if context.response else {}).items()}
        challenged = status == 403 or "peakhour-challenge" in headers
        try:
            await context.page.wait_for_function(ready_js, timeout=(CHALLENGE_WAIT_SECS if challenged else cfg.page_timeout_secs) * 1000)
            ready = True
        except Exception:  # noqa: BLE001 - timeout: fall through to the block check
            ready = False
        if ready:
            # The redirect after a solved challenge is a fresh navigation; make sure its document
            # is fully parsed before reading it, or page.content() returns a truncated page.
            try:
                await context.page.wait_for_load_state("domcontentloaded", timeout=cfg.page_timeout_secs * 1000)
            except Exception:  # noqa: BLE001
                pass
        html = await context.page.content()
        title = await context.page.title()
        if not ready and looks_blocked(status, title, html):
            state.blocked_hits += 1
            raise SessionError(
                f"Blocked by bot mitigation (HTTP {status}, title={title!r}, {len(html)} bytes, "
                f"challenge {'not solved' if challenged else 'not offered'}) at {context.request.url}"
            )
        solved = challenged and ready
        if solved:
            state.challenges_solved += 1
            Actor.log.info(f"Peakhour challenge solved in the browser for {context.request.url}")
        return status, title, html, solved

    async def capture_handoff(context: PlaywrightCrawlingContext) -> None:
        nonlocal handoff
        cookies = await context.page.context.cookies()
        handoff = cookies_to_handoff(
            cookies,
            ua_by_page.get(id(context.page)),
            context.session.id if context.session else None,
            context.proxy_info.url if context.proxy_info else None,
            context.request.url,
        )

    @crawler.router.default_handler
    async def handle_search(context: PlaywrightCrawlingContext) -> None:
        url = context.request.url
        page_num = int(context.request.user_data.get("page", 1))
        if state.reached:
            return
        status, title, html, _ = await settle(context, SEARCH_READY_JS)
        state.browser_pages += 1
        try:
            app_data = await context.page.evaluate(APP_DATA_SEARCH_JS)
        except Exception:  # noqa: BLE001
            app_data = None

        try:
            items, meta = parse_search_page(
                html, source_url=url, page=page_num, now=datetime.now(timezone.utc), app_data=app_data or None
            )
        except SelectorsMatchedNothing:
            state.empty_pages += 1
            raise

        if solve_only:
            leftover.searches = [s for s in leftover.searches if s[0] != url]
            await capture_handoff(context)

        if not items and meta.zero_results:
            state.zero_result_pages += 1
            Actor.log.info(f"No results on Gumtree for {url} (genuine empty search).")
            return

        state.pages_ok += 1
        fresh = state.take(items)
        Actor.log.info(
            f"Page {page_num} ({meta.source}, browser, HTTP {status}, {len(html)} bytes) {url}: {len(items)} listings, {len(fresh)} new "
            f"(total found on site: {meta.number_found}); accepted so far {state.reserved}/{cfg.max_items}"
        )
        if partial_render_suspected(meta.source, len(items), meta.number_found, meta.next_page_url):
            state.partial_pages += 1
            Actor.log.warning(
                f"Page {page_num} parsed from the DOM with only {len(items)} cards, no data blob and no next link "
                f"while Gumtree reports {meta.number_found} results: the page was probably captured before it finished rendering."
            )

        if cfg.include_description:
            with_url = [it for it in fresh if it.get("url")]
            if solve_only:
                leftover.listings = with_url + leftover.listings
            else:
                await context.add_requests(
                    [
                        Request.from_url(it["url"], label=LABEL_LISTING, user_data={"item": it}, unique_key=f"listing:{it['id']}")
                        for it in with_url
                    ]
                )
            # Items without a URL cannot be enriched; push them as they are.
            bare = [it for it in fresh if not it.get("url")]
            if bare:
                await Actor.push_data(bare)
                state.pushed += len(bare)
        elif fresh:
            await Actor.push_data(fresh)
            state.pushed += len(fresh)

        if not state.reached and meta.next_page_url:
            if solve_only:
                leftover.searches.insert(0, (meta.next_page_url, page_num + 1))
            else:
                await context.add_requests(
                    [Request.from_url(meta.next_page_url, label=LABEL_SEARCH, user_data={"page": page_num + 1})]
                )

    @crawler.router.handler(LABEL_LISTING)
    async def handle_listing(context: PlaywrightCrawlingContext) -> None:
        item = dict(context.request.user_data["item"])
        _, title, html, _ = await settle(context, LISTING_READY_JS)
        state.browser_pages += 1
        extra = parse_listing_page(html)
        if not extra.get("description") and not looks_like_gumtree(title, html):
            raise SelectorsMatchedNothing(f"Listing page unrecognised: {context.request.url}")
        if solve_only:
            leftover.listings = [it for it in leftover.listings if it.get("id") != item.get("id")]
            await capture_handoff(context)
        item.update({k: v for k, v in extra.items() if v is not None})
        item.setdefault("description", None)
        await Actor.push_data(item)
        state.pushed += 1

    @crawler.failed_request_handler
    async def on_failed(context, error: Exception) -> None:
        state.failed_requests += 1
        if isinstance(error, SessionError):
            state.blocked_hits += 1
        Actor.log.error(f"Gave up on {context.request.url}: {type(error).__name__}: {error}")
        if solve_only:
            # Nothing was solved; hand everything back untouched so the caller can decide.
            return
        if context.request.label == LABEL_LISTING:
            # Keep the card data rather than losing the listing entirely.
            item = dict(context.request.user_data["item"])
            item["description"] = None
            item["descriptionError"] = f"{type(error).__name__}: {error}"[:300]
            await Actor.push_data(item)
            state.pushed += 1

    if solve_only:
        if pending.searches:
            url, page = pending.searches[0]
            start_requests = [Request.from_url(url, label=LABEL_SEARCH, user_data={"page": page})]
        elif pending.listings:
            it = pending.listings[0]
            start_requests = [Request.from_url(it["url"], label=LABEL_LISTING, user_data={"item": it}, unique_key=f"listing:{it['id']}")]
        else:
            return None, None
    else:
        start_requests = [
            Request.from_url(url, label=LABEL_SEARCH, user_data={"page": page}) for url, page in pending.searches
        ] + [
            Request.from_url(it["url"], label=LABEL_LISTING, user_data={"item": it}, unique_key=f"listing:{it['id']}")
            for it in pending.listings
        ]
    await crawler.run(start_requests)
    if not solve_only:
        return None, None
    return (leftover if leftover else None), handoff


# --------------------------------------------------------------------------- #
# auto: HTTP once -> browser solves -> HTTP with the browser's session -> browser
# --------------------------------------------------------------------------- #
async def run_auto(
    cfg: Config,
    state: RunState,
    fetcher: HttpFetcher,
    push: PushFn,
    log: logging.Logger,
    browser: BrowserRunner,
    *,
    pending: PendingWork | None = None,
) -> None:
    pending = pending or PendingWork([(u, 1) for u in cfg.start_urls], [])

    left = await run_http(cfg, state, fetcher, push, log, pending=pending, handover_after_giveups=1, single_attempt_first=True)
    if left is None:
        return

    first = left.searches[0][0] if left.searches else (left.listings[0].get("url") if left.listings else "?")
    log.warning(f"HTTP path blocked at {first}; solving the Peakhour challenge in the browser for that page.")
    state.handed_to_browser = True
    left, handoff = await browser(left, True)
    if not left:
        return
    if handoff is None or not handoff.cookies:
        log.warning("Browser did not pass the challenge either; finishing the run in the browser with session rotation.")
        await browser(left, False)
        return

    state.handoff_attempted = True
    log.info(
        f"Challenge passed in the browser (cookies {sorted(handoff.cookies)}); offering that session to the HTTP client once."
    )
    await fetcher.adopt(handoff)
    left = await run_http(cfg, state, fetcher, push, log, pending=left, handover_after_giveups=1, single_attempt_first=True)
    if left is None:
        state.handoff_accepted = True
        return
    log.warning(
        "The browser's session was challenged again over HTTP (Peakhour binds __rp_ch to the TLS fingerprint that "
        "solved it); staying in the browser for the rest of the run."
    )
    await browser(left, False)


# --------------------------------------------------------------------------- #
# Run usage (platform only): proxy traffic, compute units, USD - for pricing
# --------------------------------------------------------------------------- #
def format_run_usage(run: dict | None) -> str | None:
    """One log line from the Actor run record (`usage` per meter, `usageTotalUsd`, `stats`)."""
    if not isinstance(run, dict):
        return None
    usage = run.get("usage") or {}
    stats = run.get("stats") or {}
    parts = []
    cu = usage.get("ACTOR_COMPUTE_UNITS")
    if cu is not None:
        parts.append(f"compute {float(cu):.4f} CU")
    for key, label in (("PROXY_RESIDENTIAL_TRANSFER_GBYTES", "residential proxy"), ("PROXY_SERPS", "SERP proxy"), ("DATA_TRANSFER_EXTERNAL_GBYTES", "external transfer")):
        val = usage.get(key)
        if val:
            parts.append(f"{label} {float(val) * 1024:.2f} MB")
    if usage.get("DATASET_WRITES"):
        parts.append(f"dataset writes {int(usage['DATASET_WRITES'])}")
    if run.get("usageTotalUsd") is not None:
        parts.append(f"total ${float(run['usageTotalUsd']):.4f}")
    if stats.get("runTimeSecs") is not None:
        parts.append(f"runtime {float(stats['runTimeSecs']):.0f} s")
    if not parts:
        return None
    return "Run usage so far: " + ", ".join(parts) + " (final figures on the run's Console row)."


async def log_run_usage() -> None:
    if not Actor.is_at_home():
        return
    try:
        run_id = Actor.configuration.actor_run_id
        run = await Actor.apify_client.run(run_id).get()
        line = format_run_usage(run)
        if line:
            Actor.log.info(line)
    except Exception as exc:  # noqa: BLE001 - reporting only
        Actor.log.debug(f"Run usage unavailable: {exc}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        cfg = config_from_input(inp)
        state = RunState(max_items=cfg.max_items)
        Actor.log.info(
            f"Start URLs: {cfg.start_urls} | maxItems={cfg.max_items} includeDescription={cfg.include_description} "
            f"fetchMode={cfg.fetch_mode} retries={cfg.max_retries}"
            + (f" httpProfiles={cfg.http_profiles}" if cfg.http_profiles else "")
        )

        proxy_configuration = await make_proxy_configuration(inp.get("proxyConfiguration"))
        pending = PendingWork([(u, 1) for u in cfg.start_urls], [])

        async def browser(work: PendingWork, solve_only: bool):
            return await run_browser(cfg, state, proxy_configuration, work, solve_only=solve_only)

        fetcher: HttpFetcher | None = None
        if cfg.fetch_mode in ("auto", "http"):
            proxy_url_for = None
            if proxy_configuration is not None:

                async def proxy_url_for(session_id: str) -> str | None:  # noqa: F811
                    return await proxy_configuration.new_url(session_id=session_id)

            def on_blocked(kind: str, status: int | None) -> None:
                state.blocked_hits += 1

            fetcher = HttpFetcher(
                proxy_url_for=proxy_url_for,
                profiles=cfg.http_profiles,
                timeout_secs=cfg.page_timeout_secs,
                max_retries=cfg.max_retries,
                delay_secs=cfg.delay_secs,
                on_blocked=on_blocked,
                logger=Actor.log,
            )
        try:
            if cfg.fetch_mode == "http":
                await run_http(cfg, state, fetcher, Actor.push_data, Actor.log, pending=pending)
            elif cfg.fetch_mode == "browser":
                await run_browser(cfg, state, proxy_configuration, pending)
            else:
                await run_auto(cfg, state, fetcher, Actor.push_data, Actor.log, browser, pending=pending)
        finally:
            if fetcher is not None:
                await fetcher.close()
                Actor.log.info(
                    f"HTTP path: {fetcher.stats.requests} requests, {fetcher.stats.blocked} blocked, "
                    f"{fetcher.stats.rotations} session rotations, {fetcher.stats.transport_errors} transport errors, "
                    f"by profile {fetcher.stats.by_profile}"
                )

        summary = (
            f"Done: {state.pushed} listings pushed, {state.pages_ok} result pages parsed "
            f"({state.http_pages} pages over HTTP, {state.browser_pages} over the browser, "
            f"{state.challenges_solved} challenges solved"
            + (", browser session accepted over HTTP" if state.handoff_accepted else (", browser session refused over HTTP" if state.handoff_attempted else ""))
            + f"), {state.zero_result_pages} genuine empty searches, {state.blocked_hits} blocked responses, "
            f"{state.empty_pages} unparseable pages, {state.partial_pages} half-rendered pages, {state.failed_requests} requests failed."
        )
        Actor.log.info(summary)
        await log_run_usage()
        failure = evaluate_run(state)
        if failure:
            Actor.log.error(failure)
            await Actor.fail(exit_code=1, status_message=failure)
            return
        await Actor.set_status_message(summary)
