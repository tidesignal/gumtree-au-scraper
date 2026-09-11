"""HTTP fetch path: curl_cffi with browser TLS/HTTP-2 impersonation, no Chromium.

Why this exists (measured 2026-09-12, Sydney residential IP, and confirmed on the
Apify platform with RESIDENTIAL/AU proxies the same day): gumtree.com.au's bot
mitigation (Peakhour) decides on the *client fingerprint*, not the IP. Headless
Chromium/Playwright got HTTP 403 through ten residential sessions; a plain
curl_cffi request that presents a real browser's TLS ClientHello and HTTP/2
SETTINGS gets the page. Which impersonation profile passes is itself
fingerprint-specific, and it changes as Peakhour's database is updated, hence
the *ladder* of profiles below rather than a single one.

Local results, fresh session per request, search page /s-rtx+4070/k0 and a
listing page (2026-09-12):

    profile             HTTP/2         HTTP/1.1
    chrome (=chrome146) 403 challenge  403 challenge
    chrome136           403 challenge  403 challenge
    chrome131           403 blocked    403 challenge
    chrome124           403 blocked    200 OK          <- in ladder
    chrome120           200 OK         (not tried)     <- in ladder
    safari (=safari260) 403 challenge  403 challenge
    safari18_0          200 OK         200 OK          <- first in ladder
    safari17_0          200 OK  (served the Next.js SRP variant)
    safari15_5          200 OK         (not tried)     <- in ladder
    edge101             403 challenge  200 then 403    <- unstable, excluded
    edge (latest)       403 challenge  403 (search) / 200 (listing)
    firefox133/latest   403 challenge  403 challenge

"403 challenge" = HTTP 403, header ``peakhour-challenge: 1``, ~31 KB obfuscated
JavaScript page with no <title> that fingerprints WebGL/canvas and POSTs the
result back with a ``Peakhour-Challenge`` request header. "403 blocked" = HTTP
403, header ``peakhour-error: blocked``, empty body. Both set a
``PEAKHOUR_VISIT`` cookie; so does every real page (it carries a beacon script),
so the cookie is not a block signal.

Rotation policy: a blocked/challenged/rate-limited response, or a transport
error, discards the session (cookie jar + proxy session id + profile) and
retries with the next profile on the ladder and a fresh proxy session, up to
``max_retries`` times. 429 also backs off before retrying.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .parser import BlockedPage, detect_block

log = logging.getLogger("apify.gumtree.http")

# (impersonate target, HTTP version) pairs, in the order they are tried.
# Verified 2026-09-12; see the module docstring. Only targets the installed
# curl_cffi knows are kept, so a curl_cffi upgrade that drops one cannot break
# the run at import time.
PROFILE_LADDER: tuple[tuple[str, str], ...] = (
    ("safari18_0", "h2"),
    ("chrome124", "http1.1"),
    ("chrome120", "h2"),
    ("safari15_5", "h2"),
)

try:  # curl_cffi is a runtime dependency; the import guard keeps the parser tests runnable without it.
    from curl_cffi import CurlHttpVersion
    from curl_cffi.requests import AsyncSession
    from curl_cffi.requests.impersonate import BrowserType

    AVAILABLE_PROFILES: frozenset[str] = frozenset(b.value for b in BrowserType)
    _HTTP_VERSIONS = {"h2": CurlHttpVersion.V2TLS, "http1.1": CurlHttpVersion.V1_1}
except ImportError:  # pragma: no cover
    AsyncSession = None  # type: ignore[assignment]
    AVAILABLE_PROFILES = frozenset()
    _HTTP_VERSIONS = {}


def available_ladder(ladder: tuple[tuple[str, str], ...] = PROFILE_LADDER) -> tuple[tuple[str, str], ...]:
    kept = tuple(p for p in ladder if p[0] in AVAILABLE_PROFILES)
    return kept or ladder  # if curl_cffi is missing entirely, keep the names for the error message


# Navigation headers a browser sends on a top-level document load. curl_cffi
# already sets the profile's own User-Agent and, for Chromium targets, the
# matching sec-ch-ua / sec-ch-ua-mobile / sec-ch-ua-platform trio, so those
# are deliberately not overridden here (a hand-written sec-ch-ua that disagrees
# with the impersonated User-Agent is itself a bot signal).
_NAV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-AU,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def extract_title(html: str | None) -> str:
    m = _TITLE_RE.search(html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


class FetchError(Exception):
    """Transport-level failure (timeout, connection reset, proxy error) after all retries."""


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    title: str
    headers: dict[str, str]
    profile: str
    http_version: str
    attempts: int
    elapsed_secs: float


@dataclass
class _Session:
    profile: str
    http_version: str
    session_id: str
    proxy_url: str | None
    client: Any
    requests_made: int = 0


@dataclass
class FetchStats:
    requests: int = 0
    blocked: int = 0
    rotations: int = 0
    transport_errors: int = 0
    by_profile: dict[str, int] = field(default_factory=dict)


ProxyUrlFor = Callable[[str], Any]  # session_id -> proxy URL (str | None), sync or awaitable
SessionFactory = Callable[[str, str, str | None, float], Any]


def default_session_factory(profile: str, http_version: str, proxy_url: str | None, timeout: float):
    if AsyncSession is None:  # pragma: no cover
        raise RuntimeError("curl_cffi is not installed; `pip install curl_cffi` or use fetchMode=browser")
    kwargs: dict[str, Any] = {
        "impersonate": profile,
        "http_version": _HTTP_VERSIONS[http_version],
        "timeout": timeout,
        "allow_redirects": True,
        "max_redirects": 5,
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return AsyncSession(**kwargs)


class HttpFetcher:
    """Fetch pages with one impersonation profile + cookie jar + proxy session at a time."""

    def __init__(
        self,
        *,
        proxy_url_for: ProxyUrlFor | None = None,
        profiles: tuple[tuple[str, str], ...] | None = None,
        timeout_secs: float = 60.0,
        max_retries: int = 3,
        delay_secs: float = 2.0,
        session_factory: SessionFactory = default_session_factory,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_blocked: Callable[[str, int | None], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.proxy_url_for = proxy_url_for
        self.profiles = profiles or available_ladder()
        if not self.profiles:
            raise ValueError("No impersonation profiles available")
        self.timeout_secs = timeout_secs
        self.max_retries = max(0, int(max_retries))
        self.delay_secs = max(0.0, float(delay_secs))
        self._session_factory = session_factory
        self._sleep = sleep
        self._on_blocked = on_blocked
        self.log = logger or log
        self.stats = FetchStats()
        self._profile_index = 0
        self._session: _Session | None = None
        self._first_request_done = False

    # ---- session management -------------------------------------------------
    async def _proxy_url(self, session_id: str) -> str | None:
        if self.proxy_url_for is None:
            return None
        result = self.proxy_url_for(session_id)
        if inspect.isawaitable(result):
            result = await result
        return result or None

    async def _open_session(self) -> _Session:
        profile, http_version = self.profiles[self._profile_index % len(self.profiles)]
        session_id = "s" + uuid.uuid4().hex[:14]
        proxy_url = await self._proxy_url(session_id)
        client = self._session_factory(profile, http_version, proxy_url, self.timeout_secs)
        self._session = _Session(profile, http_version, session_id, proxy_url, client)
        self.log.info(
            f"HTTP session {session_id}: impersonate={profile} {http_version} "
            f"proxy={'yes' if proxy_url else 'none'}"
        )
        return self._session

    async def _discard_session(self) -> None:
        if self._session is not None:
            close = getattr(self._session.client, "close", None)
            if close is not None:
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:  # noqa: BLE001 - closing a dead session must never fail the run
                    pass
            self._session = None

    async def rotate(self, reason: str) -> None:
        await self._discard_session()
        self._profile_index += 1
        self.stats.rotations += 1
        nxt = self.profiles[self._profile_index % len(self.profiles)]
        self.log.warning(f"Rotating session ({reason}); next profile {nxt[0]}/{nxt[1]}")

    async def close(self) -> None:
        await self._discard_session()

    @property
    def current_profile(self) -> tuple[str, str]:
        return self.profiles[self._profile_index % len(self.profiles)]

    # ---- fetching -------------------------------------------------------------
    async def _polite_delay(self) -> None:
        if self.delay_secs > 0 and self._first_request_done:
            await self._sleep(random.uniform(0.5, 1.5) * self.delay_secs)
        self._first_request_done = True

    async def fetch(self, url: str, *, referer: str | None = None) -> FetchResult:
        """GET one page; rotate on block/429/transport error; raise after max_retries.

        Raises BlockedPage when the final attempt was blocked, FetchError when it
        was a transport failure.
        """
        headers = dict(_NAV_HEADERS)
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        else:
            headers["Sec-Fetch-Site"] = "none"

        attempts = self.max_retries + 1
        last_block: tuple[str, int | None] | None = None
        last_error: Exception | None = None
        started = time.monotonic()
        for attempt in range(1, attempts + 1):
            session = self._session or await self._open_session()
            await self._polite_delay()
            self.stats.requests += 1
            self.stats.by_profile[session.profile] = self.stats.by_profile.get(session.profile, 0) + 1
            try:
                resp = await session.client.get(url, headers=headers)
            except Exception as exc:  # noqa: BLE001 - curl errors are library-specific
                last_error = exc
                self.stats.transport_errors += 1
                self.log.warning(f"Transport error on {url} (attempt {attempt}/{attempts}): {type(exc).__name__}: {exc}")
                if attempt < attempts:
                    await self.rotate("transport error")
                continue
            session.requests_made += 1
            status = int(getattr(resp, "status_code", 0) or 0)
            resp_headers = {str(k).lower(): str(v) for k, v in dict(getattr(resp, "headers", {}) or {}).items()}
            html = getattr(resp, "text", "") or ""
            title = extract_title(html)
            kind = detect_block(status, resp_headers, html, title)
            if kind is None:
                return FetchResult(
                    url=url,
                    status=status,
                    html=html,
                    title=title,
                    headers=resp_headers,
                    profile=session.profile,
                    http_version=session.http_version,
                    attempts=attempt,
                    elapsed_secs=round(time.monotonic() - started, 2),
                )
            last_block = (kind, status)
            self.stats.blocked += 1
            if self._on_blocked is not None:
                self._on_blocked(kind, status)
            self.log.warning(
                f"Blocked ({kind}, HTTP {status}, {len(html)} bytes, profile {session.profile}/{session.http_version}, "
                f"attempt {attempt}/{attempts}) at {url}"
            )
            if attempt < attempts:
                if kind == "rate_limited":
                    await self._sleep(min(30.0, 2.0 * 2 ** (attempt - 1)))
                await self.rotate(kind)
        # Exhausted.
        await self._discard_session()
        if last_block is not None:
            kind, status = last_block
            exc = BlockedPage(f"Blocked by bot mitigation after {attempts} attempt(s) ({kind}, last HTTP {status}): {url}")
            exc.kind = kind  # type: ignore[attr-defined]
            exc.status = status  # type: ignore[attr-defined]
            exc.attempts = attempts  # type: ignore[attr-defined]
            raise exc
        raise FetchError(f"Fetch failed after {attempts} attempt(s): {url}: {type(last_error).__name__}: {last_error}")
