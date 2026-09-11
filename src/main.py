"""Gumtree Australia Scraper - Apify Actor entry point.

Fetch layer : Crawlee PlaywrightCrawler (headless Chromium) with browser-fingerprint
              injection, session rotation on 403/429, and Apify Proxy (residential,
              AU) by default. Local runs without proxy credentials fall back to a
              direct connection and say so.
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
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlencode, urlparse

from apify import Actor
from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from crawlee.errors import SessionError
from crawlee.fingerprint_suite import DefaultFingerprintGenerator, HeaderGeneratorOptions

from .parser import (
    BASE_URL,
    SelectorsMatchedNothing,
    looks_blocked,
    looks_like_gumtree,
    parse_listing_page,
    parse_search_page,
)

LABEL_SEARCH = "SEARCH"
LABEL_LISTING = "LISTING"

# Location IDs verified on gumtree.com.au (2026-09-11). Anything else must be
# passed as the numeric id from a Gumtree URL.
LOCATION_ALIASES = {"sydney": "3003435", "sydney region": "3003435"}

APP_DATA_READY_JS = "() => !!(window.APP_DATA && window.APP_DATA.search && window.APP_DATA.search.results)"
APP_DATA_SEARCH_JS = "() => ({ search: window.APP_DATA.search })"


# --------------------------------------------------------------------------- #
# Input handling
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    start_urls: list[str]
    max_items: int = 100
    include_description: bool = False
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
    return Config(
        start_urls=urls,
        max_items=max(1, int(inp.get("maxItems") or 100)),
        include_description=bool(inp.get("includeDescription", False)),
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
            "running WITHOUT proxy. Expect gumtree.com.au to refuse headless traffic from most networks."
        )
        return None
    try:
        return await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
    except Exception as exc:  # noqa: BLE001
        Actor.log.warning(f"Could not create proxy configuration ({exc}); running WITHOUT proxy.")
        return None


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        cfg = config_from_input(inp)
        state = RunState(max_items=cfg.max_items)
        Actor.log.info(
            f"Start URLs: {cfg.start_urls} | maxItems={cfg.max_items} "
            f"includeDescription={cfg.include_description} retries={cfg.max_retries}"
        )

        proxy_configuration = await make_proxy_configuration(inp.get("proxyConfiguration"))

        crawler = PlaywrightCrawler(
            browser_type="chromium",
            headless=True,
            browser_launch_options={"args": ["--disable-blink-features=AutomationControlled"]},
            browser_new_context_options={"locale": "en-AU", "timezone_id": "Australia/Sydney"},
            fingerprint_generator=DefaultFingerprintGenerator(
                header_options=HeaderGeneratorOptions(browsers=["chrome"], locales=["en-AU"]),
            ),
            navigation_timeout=timedelta(seconds=cfg.page_timeout_secs),
            request_handler_timeout=timedelta(seconds=cfg.page_timeout_secs + 30),
            proxy_configuration=proxy_configuration,
            use_session_pool=True,
            retry_on_blocked=True,
            max_request_retries=cfg.max_retries,
            concurrency_settings=ConcurrencySettings(desired_concurrency=1, max_concurrency=2),
        )

        @crawler.pre_navigation_hook
        async def polite_delay(context: PlaywrightCrawlingContext) -> None:
            if cfg.delay_secs > 0:
                await asyncio.sleep(random.uniform(0.5, 1.5) * cfg.delay_secs)

        async def read_page(context: PlaywrightCrawlingContext) -> tuple[int | None, str, str]:
            status = context.response.status if context.response else None
            html = await context.page.content()
            title = await context.page.title()
            return status, title, html

        async def assert_not_blocked(context: PlaywrightCrawlingContext, status, title, html) -> None:
            if looks_blocked(status, title, html):
                state.blocked_hits += 1
                raise SessionError(
                    f"Blocked by bot mitigation (HTTP {status}, title={title!r}, {len(html)} bytes) at {context.request.url}"
                )

        @crawler.router.default_handler
        async def handle_search(context: PlaywrightCrawlingContext) -> None:
            url = context.request.url
            page_num = int(context.request.user_data.get("page", 1))
            if state.reached:
                return

            app_data = None
            try:
                await context.page.wait_for_function(APP_DATA_READY_JS, timeout=cfg.page_timeout_secs * 1000)
                app_data = await context.page.evaluate(APP_DATA_SEARCH_JS)
            except Exception:  # noqa: BLE001 - timeout: fall through to block check / DOM fallback
                app_data = None
            status, title, html = await read_page(context)
            if app_data is None:
                await assert_not_blocked(context, status, title, html)

            try:
                items, meta = parse_search_page(
                    html, source_url=url, page=page_num, now=datetime.now(timezone.utc), app_data=app_data
                )
            except SelectorsMatchedNothing:
                state.empty_pages += 1
                raise

            if not items and meta.zero_results:
                state.zero_result_pages += 1
                Actor.log.info(f"No results on Gumtree for {url} (genuine empty search).")
                return

            state.pages_ok += 1
            fresh = state.take(items)
            Actor.log.info(
                f"Page {page_num} ({meta.source}) {url}: {len(items)} listings, {len(fresh)} new "
                f"(total found on site: {meta.number_found}); accepted so far {state.reserved}/{cfg.max_items}"
            )

            if cfg.include_description:
                await context.add_requests(
                    [
                        Request.from_url(it["url"], label=LABEL_LISTING, user_data={"item": it}, unique_key=f"listing:{it['id']}")
                        for it in fresh
                        if it.get("url")
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
                await context.add_requests(
                    [Request.from_url(meta.next_page_url, label=LABEL_SEARCH, user_data={"page": page_num + 1})]
                )

        @crawler.router.handler(LABEL_LISTING)
        async def handle_listing(context: PlaywrightCrawlingContext) -> None:
            item = dict(context.request.user_data["item"])
            status, title, html = await read_page(context)
            await assert_not_blocked(context, status, title, html)
            extra = parse_listing_page(html)
            if not extra.get("description") and not looks_like_gumtree(title, html):
                raise SelectorsMatchedNothing(f"Listing page unrecognised: {context.request.url}")
            item.update({k: v for k, v in extra.items() if v is not None})
            item.setdefault("description", None)
            await Actor.push_data(item)
            state.pushed += 1

        @crawler.failed_request_handler
        async def on_failed(context, error: Exception) -> None:
            state.failed_requests += 1
            if isinstance(error, SessionError):
                # Crawlee raises this itself on 401/403/429 before our handler sees the page.
                state.blocked_hits += 1
            Actor.log.error(f"Gave up on {context.request.url}: {type(error).__name__}: {error}")
            if context.request.label == LABEL_LISTING:
                # Keep the card data rather than losing the listing entirely.
                item = dict(context.request.user_data["item"])
                item["description"] = None
                item["descriptionError"] = f"{type(error).__name__}: {error}"[:300]
                await Actor.push_data(item)
                state.pushed += 1

        start_requests = [
            Request.from_url(url, label=LABEL_SEARCH, user_data={"page": 1}) for url in cfg.start_urls
        ]
        await crawler.run(start_requests)

        summary = (
            f"Done: {state.pushed} listings pushed, {state.pages_ok} result pages parsed, "
            f"{state.zero_result_pages} genuine empty searches, {state.blocked_hits} blocked responses, "
            f"{state.empty_pages} unparseable pages, {state.failed_requests} requests failed."
        )
        Actor.log.info(summary)
        failure = evaluate_run(state)
        if failure:
            Actor.log.error(failure)
            await Actor.fail(exit_code=1, status_message=failure)
            return
        await Actor.set_status_message(summary)
