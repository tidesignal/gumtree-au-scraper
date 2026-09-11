"""Pure parsing for gumtree.com.au (Australia) search-result and listing pages.

Nothing in this module touches the network: every function takes a string or a
dict and returns plain Python, so all of it is unit-tested against saved real
pages in tests/fixtures/.

Where the data comes from (verified 2026-09-11 in a real Chrome session)
--------------------------------------------------------------------------
Search results page (SRP), e.g. /s-rtx+4070/k0

* Primary source: the inline ``window.APP_DATA = {...}`` JSON blob the React
  app is hydrated from.
    APP_DATA.search.results.main   organic listings, 24 per page
    APP_DATA.search.results.top    promoted "top ads" rendered above them
    APP_DATA.search.searchMeta     numberFound, pagination.nextPageUrl (absolute,
                                   null on the last page), zeroSearchResults
  Each listing carries id, title, description (card snippet), priceText,
  priceType (FIXED | NEGOTIABLE | SWAP_TRADE | GIVE_AWAY), location /
  locationArea / locationState, age ("8 hours ago", "Yesterday", "09/09/2026"),
  mainImageUrl, url ("/web/listing/<category-slug>/<id>"), isWanted, isFree,
  isFeatured, isB2CPlus, isPostedByCarDealer, isPriceDrop, previousPriceString.
  ``price`` is always "" in this blob, so the number is parsed from priceText.

* Fallback source: the rendered cards.
    a.user-ad-row-new-design                 one per listing; id="user-ad-<id>",
                                             href="/web/listing/<category>/<id>"
    .user-ad-row-new-design__title-span      title
    .user-ad-price-new-design__price         "$950", "Swap/Trade", "Free"
    .user-ad-price-new-design__negotiable-label   present when negotiable
    .user-ad-row-new-design__location        "Dural, NSW"
    .user-ad-row-new-design__age             "8 hours ago" / "09/09/2026"
    .user-ad-row-new-design__description-text     snippet
    div.fuse-ads                             sponsored blocks (siblings of the
                                             cards, never anchors, so the card
                                             selector skips them by construction)
    a.page-number-navigation__link-next      next page
    h1.breadcrumbs__summary--enhanced        "33 Results: rtx 4070 in Australia"

Listing page (VIP), e.g. /web/listing/components/1344519187 (a separate Next.js app)

* ``<script type="application/ld+json">`` Product: name, description,
  offers.price (number), offers.priceCurrency, offers.itemCondition,
  offers.availableAtOrFrom.address (postalCode, addressLocality, addressRegion).
* ``<script id="__NEXT_DATA__">`` props.pageProps.vipData.data: description
  (with newlines preserved), adPriceData {amount, currency, type, priceText,
  isFree}, adLocationData {suburb, state, postcode}, summaryInfo [{name:
  "Condition", value: "Used"}, ...], adPosterData {posterType: "PRIVATE",
  proseller, carDealer}, status, categoryName.
  The seller's name, profile URL and avatar are also in there and are
  deliberately not read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.gumtree.com.au"
CURRENCY = "AUD"

CARD_SELECTOR = "a.user-ad-row-new-design"
NEXT_PAGE_SELECTOR = "a.page-number-navigation__link-next"
RESULT_COUNT_SELECTOR = "h1.breadcrumbs__summary--enhanced"

try:  # Gumtree dates are Sydney-local; tzdata is in requirements for Windows.
    from zoneinfo import ZoneInfo

    SYDNEY = ZoneInfo("Australia/Sydney")
except Exception:  # pragma: no cover - only if tzdata is missing
    SYDNEY = timezone(timedelta(hours=10))


class SelectorsMatchedNothing(Exception):
    """The page loaded and looks like Gumtree, but no listing could be parsed.

    This is treated as a scraper bug (DOM change, wrong page), never as
    "no results": a genuine empty search carries zeroSearchResults=true.
    """


class BlockedPage(Exception):
    """A bot-mitigation (Peakhour) block or challenge page came back instead of results."""


# --------------------------------------------------------------------------- #
# Block / page-type detection
# --------------------------------------------------------------------------- #
_BLOCK_MARKERS = (
    "Peakhour",
    "PEAKHOUR_VISIT",
    "Access has been denied to the requested page",
)


def looks_blocked(status: int | None, title: str | None, html: str | None) -> bool:
    """True for the responses Peakhour serves to traffic it does not like.

    Observed 2026-09-11 from Playwright: HTTP 403 with an empty body, HTTP 403
    with a 31 KB obfuscated JS challenge (title empty), and HTTP 403 with a
    1.6 KB "Access denied" page that prints a Request ID and the client IP.
    """
    if status in (401, 403, 429, 503):
        return True
    t = (title or "").strip().lower()
    body = html or ""
    if t == "access denied":
        return True
    if not t and len(body) < 5000:
        return True
    return any(marker in body for marker in _BLOCK_MARKERS)


def looks_like_gumtree(title: str | None, html: str | None) -> bool:
    return "gumtree" in (title or "").lower() or "gumtree.com.au" in (html or "")


# --------------------------------------------------------------------------- #
# APP_DATA extraction
# --------------------------------------------------------------------------- #
_APP_DATA_RE = re.compile(r"window\.APP_DATA\s*=\s*")


def extract_app_data(html: str) -> dict | None:
    """Return the ``window.APP_DATA`` object embedded in a search page, or None."""
    if not html:
        return None
    m = _APP_DATA_RE.search(html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, m.end())
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# --------------------------------------------------------------------------- #
# Small field parsers
# --------------------------------------------------------------------------- #
_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")


def parse_price(text: str | None) -> int | float | None:
    """'$1,800' -> 1800, '$19.99' -> 19.99, 'Swap/Trade' / '' -> None."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return int(value) if value.is_integer() else value


_REL_RE = re.compile(r"(\d+)\s*(minute|min|hour|hr|day|week|month|year)s?\s*ago", re.I)
_DMY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def parse_posted_at(raw: str | None, now: datetime | None = None) -> str | None:
    """Turn Gumtree's relative/absolute age text into ISO 8601.

    Relative times ("8 hours ago") become a full timestamp; day-level values
    ("Yesterday", "09/09/2026", "2 weeks ago") become a date, because that is
    all the precision Gumtree gives. Unknown formats return None; the raw text
    is always kept next to it in the output.
    """
    s = (raw or "").strip()
    if not s:
        return None
    now = (now or datetime.now(timezone.utc)).astimezone(SYDNEY)
    low = s.lower()
    if low in ("just now", "now", "moments ago"):
        return now.isoformat(timespec="seconds")
    if low == "today":
        return now.date().isoformat()
    if low == "yesterday":
        return (now - timedelta(days=1)).date().isoformat()
    m = _REL_RE.search(low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit in ("minute", "min"):
            return (now - timedelta(minutes=n)).isoformat(timespec="seconds")
        if unit in ("hour", "hr"):
            return (now - timedelta(hours=n)).isoformat(timespec="seconds")
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        return (now - timedelta(days=days)).date().isoformat()
    m = _DMY_RE.match(s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    return None


def category_from_url(url: str | None) -> str | None:
    """'/web/listing/components/1344519187' -> 'components'."""
    if not url:
        return None
    m = re.search(r"/web/listing/([^/]+)/\d+", url)
    return m.group(1) if m else None


def id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"/(\d{6,})(?:[/?#]|$)", url)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Classification: "can this row be read as a price observation?"
# --------------------------------------------------------------------------- #
# Wanted ads are demand, not supply - their price points the other way. The
# regexes are generic on purpose; Gumtree's own isWanted flag is checked first.
_WANTED_RE = re.compile(
    r"^\s*(wanted\b|wtb\b)|\bwtb\b|want(ed)? to buy|looking (for|to buy)|\bISO\b|in search of",
    re.I,
)
_DEAD_RE = re.compile(
    r"parts only|for parts|spares? or repairs?|\bfault(y|ed)\b|\bnot working\b|"
    r"\bdead\b|\bbroken\b|\bcracked\b|water damage|as[- ]is\b|needs? repair|for repair",
    re.I,
)
_BUNDLE_RE = re.compile(r"\bcombo\b|\bbundle\b|\bjob\s*lot\b|\blot of\b|\bbulk\s*lot\b", re.I)
_INWORD_PUNCT = re.compile(r"(?<=[A-Za-z])[,.](?=[A-Za-z])")  # "electric,ian" -> "electrician"


def classify(
    title: str | None,
    price_type: str | None = None,
    is_wanted: bool = False,
    is_free: bool = False,
) -> str:
    """One of: wanted | swap | free | dead | bundle | item (checked in that order).

    Only ``item`` rows should feed a "what does X sell for" series; the rest
    are kept in the output (with the reason) but flagged priceable=False.
    """
    t = _INWORD_PUNCT.sub("", title or "")
    if is_wanted or _WANTED_RE.search(t):
        return "wanted"
    if price_type == "SWAP_TRADE":
        return "swap"
    if is_free or price_type == "GIVE_AWAY":
        return "free"
    if _DEAD_RE.search(t):
        return "dead"
    if _BUNDLE_RE.search(t):
        return "bundle"
    return "item"


# --------------------------------------------------------------------------- #
# Item assembly
# --------------------------------------------------------------------------- #
@dataclass
class SearchMeta:
    number_found: int | None = None
    next_page_url: str | None = None
    is_last_page: bool = True
    zero_results: bool = False
    current_page: int | None = None
    last_page: int | None = None
    category_name: str | None = None
    source: str = "app_data"  # or "dom"


def _seller_type(is_b2c: bool, is_dealer: bool) -> str | None:
    if is_dealer:
        return "dealer"
    if is_b2c:
        return "business"
    return None


def build_item(
    *,
    id: str,
    title: str,
    price_text: str | None,
    price_type: str | None,
    is_negotiable: bool,
    suburb: str | None,
    area: str | None,
    state: str | None,
    posted_raw: str | None,
    url: str | None,
    image_url: str | None,
    image_urls: list[str] | None,
    snippet: str | None,
    is_wanted: bool,
    is_free: bool,
    is_promoted: bool,
    is_featured: bool,
    is_urgent: bool,
    is_price_drop: bool,
    previous_price_text: str | None,
    seller_type: str | None,
    source_url: str,
    page: int,
    now: datetime,
) -> dict[str, Any]:
    price = parse_price(price_text)
    if price_type is None:
        if is_free or (price_text or "").strip().lower() == "free":
            price_type = "GIVE_AWAY"
        elif (price_text or "").strip().lower().startswith("swap"):
            price_type = "SWAP_TRADE"
        elif price is not None:
            price_type = "NEGOTIABLE" if is_negotiable else "FIXED"
    kind = classify(title, price_type, is_wanted, is_free)
    location = ", ".join(p for p in (suburb, state) if p) or None
    return {
        "id": str(id),
        "title": title,
        "price": price,
        "priceText": price_text or "",
        "priceType": price_type,
        "currency": CURRENCY,
        "isNegotiable": bool(is_negotiable),
        "isFree": bool(is_free or price_type == "GIVE_AWAY"),
        "isWanted": bool(is_wanted or kind == "wanted"),
        "isSwap": price_type == "SWAP_TRADE",
        "isPromoted": bool(is_promoted),
        "isFeatured": bool(is_featured),
        "isUrgent": bool(is_urgent),
        "isPriceDrop": bool(is_price_drop),
        "previousPriceText": previous_price_text or None,
        "location": location,
        "suburb": suburb or None,
        "area": area or None,
        "state": state or None,
        "postedAt": posted_raw or None,
        "postedAtIso": parse_posted_at(posted_raw, now),
        "url": urljoin(BASE_URL, url) if url else None,
        "imageUrl": image_url or None,
        "imageUrls": image_urls or [],
        "category": category_from_url(url),
        "sellerType": seller_type,
        "snippet": snippet or None,
        "kind": kind,
        "priceable": kind == "item" and price is not None,
        "searchUrl": source_url,
        "page": page,
        "scrapedAt": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
    }


def _item_from_app_data(raw: dict, *, promoted: bool, source_url: str, page: int, now: datetime) -> dict | None:
    title = (raw.get("title") or "").strip()
    price_text = (raw.get("priceText") or "").strip()
    if not title and not price_text:
        return None  # an empty row poisons every downstream diff; never emit it
    listing_id = str(raw.get("id") or id_from_url(raw.get("url")) or "")
    if not listing_id:
        return None
    extra = [u for u in (raw.get("extraImageUrls") or []) if isinstance(u, str)]
    return build_item(
        id=listing_id,
        title=title,
        price_text=price_text,
        price_type=raw.get("priceType") or None,
        is_negotiable=bool(raw.get("isNegotiable")),
        suburb=raw.get("location") or None,
        area=raw.get("locationArea") or None,
        state=raw.get("locationState") or None,
        posted_raw=raw.get("age") or None,
        url=raw.get("url") or None,
        image_url=raw.get("mainImageUrl") or None,
        image_urls=extra,
        snippet=(raw.get("description") or "").strip() or None,
        is_wanted=bool(raw.get("isWanted")),
        is_free=bool(raw.get("isFree")),
        is_promoted=promoted,
        is_featured=bool(raw.get("isFeatured")),
        is_urgent=bool(raw.get("isUrgent")),
        is_price_drop=bool(raw.get("isPriceDrop")),
        previous_price_text=raw.get("previousPriceString") or None,
        seller_type=_seller_type(bool(raw.get("isB2CPlus")), bool(raw.get("isPostedByCarDealer"))),
        source_url=source_url,
        page=page,
        now=now,
    )


def parse_search_app_data(
    search: dict, *, source_url: str, page: int = 1, now: datetime | None = None
) -> tuple[list[dict], SearchMeta]:
    """Parse ``APP_DATA.search`` (already a dict) into items + pagination meta."""
    now = now or datetime.now(timezone.utc)
    results = search.get("results") or {}
    items: list[dict] = []
    for raw in results.get("top") or []:
        it = _item_from_app_data(raw, promoted=True, source_url=source_url, page=page, now=now)
        if it:
            items.append(it)
    for raw in results.get("main") or []:
        it = _item_from_app_data(raw, promoted=False, source_url=source_url, page=page, now=now)
        if it:
            items.append(it)

    meta_raw = search.get("searchMeta") or {}
    pag = meta_raw.get("pagination") or {}
    next_url = pag.get("nextPageUrl") or None
    meta = SearchMeta(
        number_found=meta_raw.get("numberFound"),
        next_page_url=urljoin(BASE_URL, next_url) if next_url else None,
        is_last_page=bool(pag.get("isLastPage", next_url is None)),
        zero_results=bool(meta_raw.get("zeroSearchResults")),
        current_page=pag.get("currentPageNum"),
        last_page=pag.get("lastPageNum"),
        category_name=meta_raw.get("categoryName") or None,
        source="app_data",
    )
    return items, meta


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def parse_search_dom(
    html: str, *, source_url: str, page: int = 1, now: datetime | None = None
) -> tuple[list[dict], SearchMeta]:
    """Fallback parser that reads the rendered listing cards."""
    now = now or datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for a in soup.select(CARD_SELECTOR):
        href = a.get("href") or ""
        listing_id = (a.get("id") or "").replace("user-ad-", "").strip() or id_from_url(href)
        title = _text(a.select_one(".user-ad-row-new-design__title-span"))
        price_text = _text(a.select_one(".user-ad-price-new-design__price"))
        if not listing_id or (not title and not price_text):
            continue
        loc_text = _text(a.select_one(".user-ad-row-new-design__location"))
        suburb, _, state = (p.strip() for p in loc_text.rpartition(","))
        if not suburb:  # "Sydney" with no comma
            suburb, state = state, ""
        img = a.select_one("img")
        items.append(
            build_item(
                id=listing_id,
                title=title,
                price_text=price_text,
                price_type=None,
                is_negotiable=a.select_one(".user-ad-price-new-design__negotiable-label") is not None,
                suburb=suburb or None,
                area=None,
                state=state or None,
                posted_raw=_text(a.select_one(".user-ad-row-new-design__age")) or None,
                url=href or None,
                image_url=(img.get("src") or img.get("data-src") or None) if img else None,
                image_urls=None,
                snippet=_text(a.select_one(".user-ad-row-new-design__description-text")) or None,
                is_wanted=False,
                is_free=price_text.strip().lower() == "free",
                is_promoted=False,
                is_featured=False,
                is_urgent=False,
                is_price_drop=False,
                previous_price_text=None,
                seller_type=None,
                source_url=source_url,
                page=page,
                now=now,
            )
        )

    next_a = soup.select_one(NEXT_PAGE_SELECTOR)
    next_url = urljoin(BASE_URL, next_a["href"]) if next_a and next_a.get("href") else None
    count = None
    h1 = soup.select_one(RESULT_COUNT_SELECTOR)
    if h1:
        m = re.search(r"([\d,]+)\s+Results?", _text(h1))
        if m:
            count = int(m.group(1).replace(",", ""))
    zero = count == 0 or bool(re.search(r"\b0 Results\b|no results found", html, re.I))
    meta = SearchMeta(
        number_found=count,
        next_page_url=next_url,
        is_last_page=next_url is None,
        zero_results=zero and not items,
        current_page=page,
        source="dom",
    )
    return items, meta


def parse_search_page(
    html: str,
    *,
    source_url: str,
    page: int = 1,
    now: datetime | None = None,
    app_data: dict | None = None,
) -> tuple[list[dict], SearchMeta]:
    """Parse one search-results page, preferring APP_DATA, falling back to the DOM.

    Raises SelectorsMatchedNothing when the page is not a genuine empty search
    yet yields no listing. Callers must treat that as a failure, not as data.
    """
    now = now or datetime.now(timezone.utc)
    data = app_data if app_data is not None else extract_app_data(html)
    search = (data or {}).get("search") if isinstance(data, dict) else None
    items: list[dict] = []
    meta = SearchMeta()
    if isinstance(search, dict) and search.get("results") is not None:
        items, meta = parse_search_app_data(search, source_url=source_url, page=page, now=now)
    if not items and not meta.zero_results:
        dom_items, dom_meta = parse_search_dom(html, source_url=source_url, page=page, now=now)
        if dom_items:
            items, meta = dom_items, dom_meta
        elif dom_meta.zero_results:
            meta = dom_meta
    if not items and not meta.zero_results:
        raise SelectorsMatchedNothing(
            f"Page loaded ({len(html or '')} bytes, gumtree={looks_like_gumtree(None, html)}) "
            f"but neither APP_DATA nor '{CARD_SELECTOR}' produced a listing: {source_url}"
        )
    return items, meta


# --------------------------------------------------------------------------- #
# Listing (VIP) page
# --------------------------------------------------------------------------- #
_CONDITION_RE = re.compile(r"schema\.org/(\w+?)Condition$")


def parse_listing_page(html: str) -> dict[str, Any]:
    """Extract the extra fields a listing page offers on top of the search card.

    Returns a dict with (some of): description, price, priceType, currency,
    condition, suburb, state, postcode, sellerType, categoryName, listingStatus.
    Missing pieces are simply absent; the caller merges over the card item.
    """
    out: dict[str, Any] = {}
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")

    # 1) JSON-LD Product block
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.get_text() or "")
        except json.JSONDecodeError:
            continue
        blocks = data if isinstance(data, list) else [data]
        for b in blocks:
            if not isinstance(b, dict) or b.get("@type") != "Product":
                continue
            if b.get("description"):
                out["description"] = str(b["description"]).strip()
            offers = b.get("offers") or {}
            if isinstance(offers, dict):
                if isinstance(offers.get("price"), (int, float)):
                    out["price"] = offers["price"]
                if offers.get("priceCurrency"):
                    out["currency"] = offers["priceCurrency"]
                m = _CONDITION_RE.search(str(offers.get("itemCondition") or ""))
                if m:
                    out["condition"] = m.group(1)
                addr = ((offers.get("availableAtOrFrom") or {}).get("address") or {})
                if isinstance(addr, dict):
                    if addr.get("postalCode"):
                        out["postcode"] = str(addr["postalCode"])
                    if addr.get("addressLocality"):
                        out["suburb"] = addr["addressLocality"]
                    if addr.get("addressRegion"):
                        out["state"] = addr["addressRegion"]

    # 2) __NEXT_DATA__ -> vipData.data (richer: newlines in description, seller type)
    nd = soup.select_one("script#__NEXT_DATA__")
    if nd is not None:
        try:
            data = (
                json.loads(nd.get_text() or "{}")
                .get("props", {})
                .get("pageProps", {})
                .get("vipData", {})
                .get("data")
            ) or {}
        except (json.JSONDecodeError, AttributeError):
            data = {}
        if isinstance(data, dict) and data:
            if data.get("description"):
                out["description"] = str(data["description"]).strip()
            price = data.get("adPriceData") or {}
            if isinstance(price.get("amount"), (int, float)):
                out["price"] = price["amount"]
            if price.get("type"):
                out["priceType"] = price["type"]
            if price.get("currency"):
                out["currency"] = price["currency"]
            loc = data.get("adLocationData") or {}
            for src, dst in (("suburb", "suburb"), ("state", "state"), ("postcode", "postcode")):
                if loc.get(src):
                    out[dst] = str(loc[src])
            for row in data.get("summaryInfo") or []:
                if isinstance(row, dict) and row.get("name") == "Condition" and row.get("value"):
                    out["condition"] = row["value"]
            poster = data.get("adPosterData") or {}
            if poster.get("carDealer"):
                out["sellerType"] = "dealer"
            elif poster.get("proseller") or str(poster.get("posterType", "")).upper() in ("BUSINESS", "COMMERCIAL", "PRO"):
                out["sellerType"] = "business"
            elif str(poster.get("posterType", "")).upper() == "PRIVATE":
                out["sellerType"] = "private"
            if data.get("categoryName"):
                out["categoryName"] = data["categoryName"]
            if data.get("status"):
                out["listingStatus"] = data["status"]
    return out
