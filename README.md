# Gumtree Australia Scraper

Turn any **gumtree.com.au** search, category or location page into a clean dataset: one row per listing with title, price as a number, suburb/state, posting date, image, category and the flags that matter when you use classifieds as market data (Wanted, free, swap, promoted).

Australia only. Not affiliated with Gumtree.

## What you get

| Field | Example | Notes |
| --- | --- | --- |
| `id` | `"1344519187"` | Gumtree listing id, unique per row |
| `title` | `"ASUS TUF Gaming RTX 4070 Ti 12GB (OC Edition)"` | |
| `price` | `950` | Number (AUD). `null` for Swap/Trade, free and "price not listed" ads |
| `priceText` | `"$950"` | Exactly as shown on the card |
| `priceType` | `"NEGOTIABLE"` | `FIXED`, `NEGOTIABLE`, `SWAP_TRADE`, `GIVE_AWAY` |
| `currency` | `"AUD"` | |
| `isNegotiable`, `isFree`, `isSwap` | `true` / `false` | |
| `isWanted` | `false` | `true` when the ad is someone looking to **buy** |
| `isPromoted`, `isFeatured`, `isUrgent`, `isPriceDrop` | `false` | Paid placement / badges |
| `previousPriceText` | `"$1,100"` | Only when Gumtree shows a price drop |
| `location`, `suburb`, `area`, `state` | `"Dural, NSW"`, `"Dural"`, `"Hornsby Area"`, `"NSW"` | |
| `postedAt` | `"8 hours ago"` | Raw text from the card |
| `postedAtIso` | `"2026-09-11T14:00:00+10:00"` | Relative times become a Sydney-time timestamp; `dd/mm/yyyy` and "Yesterday" become a date |
| `url` | `https://www.gumtree.com.au/web/listing/components/1344519187` | |
| `imageUrl`, `imageUrls` | CDN URLs | Main photo and up to two extra thumbnails |
| `category` | `"components"` | Slug from the listing URL |
| `sellerType` | `"business"`, `"dealer"` or `null` | Only when Gumtree marks the seller; `null` means not indicated |
| `snippet` | first ~500 chars | The description preview shown on the card, always included |
| `kind` | `"item"` | `wanted` / `swap` / `free` / `dead` (faulty, for parts) / `bundle` / `item` |
| `priceable` | `true` | `kind == "item"` and a numeric price: safe to use as a price observation |
| `searchUrl`, `page`, `scrapedAt` | | Provenance |

With **Fetch full description** on, each row also gets `description` (full text with line breaks), `condition` (New/Used), `postcode`, `categoryName`, `listingStatus`, and `sellerType` becomes `private` / `business` / `dealer`.

`kind` and `priceable` exist because a "Wanted: RTX 4070, $4,000" ad, a "parts only" card and a "CPU + motherboard + RAM combo" all have a price and all look like the thing you searched for, yet none of them tells you what that thing sells for. They stay in the output, flagged, so you can decide.

## Input

Either paste Gumtree URLs or describe the search.

```json
{
  "searchUrls": [{ "url": "https://www.gumtree.com.au/s-sydney/exercise+bike/k0l3003435" }],
  "maxItems": 24,
  "includeDescription": false,
  "fetchMode": "auto",
  "proxyConfiguration": { "useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"], "apifyProxyCountry": "AU" }
}
```

or

```json
{
  "keyword": "rtx 4070",
  "category": "18552",
  "location": "sydney",
  "adType": "offering",
  "sortBy": "date",
  "maxItems": 200
}
```

- **searchUrls**: any result page copied from the address bar. Filters in the URL (price range, condition, `?ad=wanted`, `?sort=price_asc`) are respected. Takes priority over the keyword fields.
- **keyword / category / location**: `category` is the number after `c` in a category URL (`/s-components/c18552`), `location` is the number after `l` at the end of a result URL (`.../k0l3003435` = Sydney Region; `sydney` is accepted as a shortcut). Unknown location names are rejected rather than guessed.
- **maxItems**: total unique listings across all start URLs. Gumtree pages hold 24 listings, so the default of 24 is one page: a cheap first run. Raise it once the output looks right.
- **includeDescription**: opens every listing page (one extra page load per row).
- **fetchMode**: `auto` (default), `http` or `browser`. See "Fetch modes" below.
- **proxyConfiguration**: see "Proxies" below.

## Output example

```json
{
  "id": "1344519187",
  "title": "ASUS TUF Gaming RTX 4070 Ti 12GB (OC Edition)",
  "price": 950,
  "priceText": "$950",
  "priceType": "NEGOTIABLE",
  "currency": "AUD",
  "isNegotiable": true,
  "isFree": false,
  "isWanted": false,
  "isSwap": false,
  "isPromoted": false,
  "isFeatured": false,
  "isUrgent": false,
  "isPriceDrop": false,
  "previousPriceText": null,
  "location": "Dural, NSW",
  "suburb": "Dural",
  "area": "Hornsby Area",
  "state": "NSW",
  "postedAt": "8 hours ago",
  "postedAtIso": "2026-09-11T14:00:00+10:00",
  "url": "https://www.gumtree.com.au/web/listing/components/1344519187",
  "imageUrl": "https://images.gumtree.com.au/image/private/t_$_35/gumtree/f879bb9c-0584-4c0a-9ae1-1c3ec7a727fa.jpg",
  "imageUrls": ["https://images.gumtree.com.au/image/private/t_$_74/gumtree/316f824b-d441-4457-ae93-c0018edabe95.jpg", "https://images.gumtree.com.au/image/private/t_$_74/gumtree/42e8ac31-cb94-4909-802d-d3c57945f55d.jpg"],
  "category": "components",
  "sellerType": null,
  "snippet": "ASUS TUF Gaming RTX 4070 Ti (OC Edition) Used in good condition - see photos Includes\nGPU\nPower splitter cable ...",
  "kind": "item",
  "priceable": true,
  "searchUrl": "https://www.gumtree.com.au/s-rtx+4070/k0",
  "page": 1,
  "scrapedAt": "2026-09-11T12:00:00+00:00"
}
```

## Use cases

- **Used-market price research**: run the same search daily, keep only `priceable` rows, and you have a real asking-price series for a GPU, a bike, a fridge. Listings that disappear are your best proxy for what actually sold.
- **Resale arbitrage**: sort by `price_asc`, filter `kind == "item"` and `condition`, compare against retail.
- **Lead generation for movers, cleaners and tradies**: "moving sale", "must go this weekend", furniture in a suburb: the `location`/`postedAtIso` fields let you act on fresh ads only. Only contact people through Gumtree itself.
- **Academic / market studies**: category-wide pulls with `isPromoted`, `sellerType`, `state` for second-hand market research.
- **Wanted-side demand**: `adType: "wanted"` gives you what people are trying to buy and what they offer.

## How it works

By default no browser is involved. A plain HTTP client (`curl_cffi`) that presents the TLS and HTTP/2 fingerprint of a real browser fetches each result page and reads the JSON the page is rendered from (`window.APP_DATA.search.results`), which is more stable than the markup. Gumtree also A/B-serves a second implementation of the same page (Next.js, `__NEXT_DATA__`); that is read too. If neither blob is present, the rendered cards are parsed. Pagination follows Gumtree's own next-page link until `maxItems` is reached or the last page is hit. Rows are de-duplicated by listing id across pages and start URLs. One HTTP session (cookie jar + proxy session + fingerprint profile) is kept for the whole run and rotated only when Gumtree blocks it.

### Fetch modes

| `fetchMode` | What happens | When to use |
| --- | --- | --- |
| `auto` (default) | HTTP client first. If it is blocked on two pages in a row after all retries, the remaining pages are fetched with headless Chromium instead. | Always, unless you are debugging. |
| `http` | HTTP client only (`curl_cffi`, browser fingerprint impersonation). No Chromium: roughly 10x cheaper in compute and proxy traffic than a browser, and a page takes well under a second. | Cheapest runs; scheduled monitoring. |
| `browser` | Headless Chromium (Playwright) for every page, with fingerprint injection and session rotation. Slower and about 1 GB of memory. | Only if `http` stops working after a change on Gumtree's side. |

Why HTTP beats a browser here: gumtree.com.au's bot mitigation (Peakhour) fingerprints the *client*, not the IP. Measured on 2026-09-12: headless Chromium got HTTP 403 on every request through ten Apify residential AU proxy sessions, while `curl_cffi` impersonating Safari 18 / Chrome 124 got the page on the first try from the same kind of connection. The HTTP client tries a short ladder of fingerprint profiles (`safari18_0` over HTTP/2, `chrome124` over HTTP/1.1, `chrome120`, `safari15_5`) and moves to the next one, with a fresh proxy session and cookie jar, whenever a response is a Peakhour challenge (`peakhour-challenge: 1`, an obfuscated JavaScript page), a hard block (`peakhour-error: blocked`, empty body) or a 429.

Selectors and data sources verified on 2026-09-11 against live pages ("rtx 4070", "exercise bike" in Sydney Region, the Components category, and a Wanted-only category page):

- Listing card: `a.user-ad-row-new-design` (`id="user-ad-<id>"`, `href="/web/listing/<category>/<id>"`)
- Title `.user-ad-row-new-design__title-span`, price `.user-ad-price-new-design__price` (also shows `Swap/Trade`), negotiable badge `.user-ad-price-new-design__negotiable-label`, location `.user-ad-row-new-design__location` ("Dural, NSW"), date `.user-ad-row-new-design__age` ("8 hours ago", "Yesterday", "09/09/2026"), snippet `.user-ad-row-new-design__description-text`
- Sponsored blocks: `div.fuse-ads` siblings between cards (never matched by the card selector)
- Next page: `a.page-number-navigation__link-next`; result count `h1.breadcrumbs__summary--enhanced`
- Listing page (`includeDescription`): JSON-LD `Product` block and `__NEXT_DATA__` -> `props.pageProps.vipData.data`
- URL grammar: `/s-<keyword>/k0`, `/s-<slug>/c<categoryId>`, `/s-<slug>/l<locationId>`, `/s-<slug>/<slug>/<keyword>/k0c<cat>l<loc>`, `?sort=date|rank|price_asc|price_desc`, `?ad=offering|wanted`, `?price-type=free`

**The scraper refuses to finish successfully with an empty dataset.** If pages load but no listing can be parsed, or every request was blocked, the run fails with a message saying so. A genuine "0 results" search (Gumtree's own `zeroSearchResults`) finishes normally with no rows. Silent empty output would look like "everything sold" to any diffing job downstream, so it is treated as an error on purpose.

## Proxies

gumtree.com.au sits behind bot mitigation (Peakhour) that fingerprints the TLS/HTTP client. The default HTTP client passes that check, so the proxy only has to look like an ordinary Australian connection: the default input uses **Apify residential proxies, country AU**, and a fresh proxy session is taken whenever a page comes back blocked. Datacenter proxies and non-AU exits are more likely to be refused. Running with no proxy works from a residential Australian connection (that is how the scraper was developed).

What was measured (2026-09-12, Sydney residential connection, fresh session per request, a search page and a listing page):

| Client | Result |
| --- | --- |
| Headless Chromium (Playwright), also via Apify residential AU proxy | HTTP 403 on every request |
| `curl_cffi` impersonating Chrome 146 / 136 / 131, Edge, Firefox, Safari 26 | HTTP 403 with a Peakhour JavaScript challenge or an empty "blocked" body |
| `curl_cffi` impersonating Safari 18.0 (HTTP/2 or HTTP/1.1), Chrome 124 (HTTP/1.1 only), Chrome 120, Safari 15.5 | HTTP 200, real page with listing data |

Peakhour's fingerprint database moves; if every profile on the ladder starts failing, the run reports it (see "Why did my run fail" below) and `fetchMode: "browser"` is the stop-gap while the ladder is updated.

## Performance and cost

One page fetch per 24 listings; a 100-listing run is 5 result pages. `includeDescription` adds one page per listing. Pages are fetched one at a time with a randomised pause between them to stay under Gumtree's rate limits; raise `requestDelaySecs` if you see 429s.

Keeping a first run cheap:

- `maxItems` defaults to **24** (one page). A default run costs one Actor start plus 24 results plus about 0.5 MB of residential proxy traffic.
- Set a **maximum cost per run** in the run options (Console: *Run options -> Max cost per run*; API: `maxTotalChargeUsd`). The platform stops the run when the cap is reached, whatever the input says; `$0.10` is plenty for a 24-item test and `$1` covers roughly 500 listings. The scraper itself does not need to know the cap.
- `fetchMode: "http"` (or the default `auto`, which is HTTP unless blocked) uses no browser, so compute is a few seconds per run instead of a Chromium process for the whole run.

## FAQ

**Can it search all of Australia?** Yes, leave `location` empty or use a URL without an `l<id>` suffix.

**How do I find a category or location id?** Open the category or region on gumtree.com.au and read the URL: `/s-components/c18552` (category 18552), `/s-sydney/l3003435` (location 3003435).

**Why is `price` null?** The ad has no numeric price: Swap/Trade, free, or "price not listed". `priceText` and `priceType` say which.

**Does it get phone numbers or seller names?** No. It reads what is on the public listing card, plus the public description and condition when `includeDescription` is on. The seller's name, profile, phone and exact coordinates are never extracted.

**Why did my run fail with "0 listings scraped"?** Every page was blocked or unparseable. The log says which: "blocked response(s) from bot mitigation" means every fingerprint profile on the HTTP ladder (and, in `auto` mode, the browser too) was refused; check the proxy setting (residential, AU) first, then try `fetchMode: "browser"`. "No listing matched the selectors" means Gumtree changed its page and the parser needs an update; open an issue with the search URL.

**Gumtree UK / NZ / South Africa?** Not supported; those are different sites with different markup.

## Legal

This actor reads publicly visible listing pages only, the same content anyone sees in a browser, and it does not log in or bypass any access control. It does not collect personal data beyond what Gumtree itself displays on a listing card (suburb, posting date, price). You are responsible for using the data in line with Gumtree's Terms of Use, its robots.txt, Australian privacy law and any applicable law in your jurisdiction; do not use it to contact people outside Gumtree's own messaging, and do not republish listing text or photos. Keep request rates modest.

Maintained by [tidesignal](https://github.com/tidesignal). Issues and selector updates: GitHub.
