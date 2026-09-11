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
  "maxItems": 100,
  "includeDescription": false,
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
- **maxItems**: total unique listings across all start URLs. Gumtree pages hold 24 listings.
- **includeDescription**: opens every listing page (one extra page load per row).
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

Headless Chromium (Playwright) loads each result page and reads the JSON the page is rendered from (`window.APP_DATA.search.results`), which is more stable than the markup. If that blob is missing, it falls back to the rendered cards. Pagination follows Gumtree's own next-page link until `maxItems` is reached or the last page is hit. Rows are de-duplicated by listing id across pages and start URLs.

Selectors and data sources verified on 2026-09-11 against live pages ("rtx 4070", "exercise bike" in Sydney Region, the Components category, and a Wanted-only category page):

- Listing card: `a.user-ad-row-new-design` (`id="user-ad-<id>"`, `href="/web/listing/<category>/<id>"`)
- Title `.user-ad-row-new-design__title-span`, price `.user-ad-price-new-design__price` (also shows `Swap/Trade`), negotiable badge `.user-ad-price-new-design__negotiable-label`, location `.user-ad-row-new-design__location` ("Dural, NSW"), date `.user-ad-row-new-design__age` ("8 hours ago", "Yesterday", "09/09/2026"), snippet `.user-ad-row-new-design__description-text`
- Sponsored blocks: `div.fuse-ads` siblings between cards (never matched by the card selector)
- Next page: `a.page-number-navigation__link-next`; result count `h1.breadcrumbs__summary--enhanced`
- Listing page (`includeDescription`): JSON-LD `Product` block and `__NEXT_DATA__` -> `props.pageProps.vipData.data`
- URL grammar: `/s-<keyword>/k0`, `/s-<slug>/c<categoryId>`, `/s-<slug>/l<locationId>`, `/s-<slug>/<slug>/<keyword>/k0c<cat>l<loc>`, `?sort=date|rank|price_asc|price_desc`, `?ad=offering|wanted`, `?price-type=free`

**The scraper refuses to finish successfully with an empty dataset.** If pages load but no listing can be parsed, or every request was blocked, the run fails with a message saying so. A genuine "0 results" search (Gumtree's own `zeroSearchResults`) finishes normally with no rows. Silent empty output would look like "everything sold" to any diffing job downstream, so it is treated as an error on purpose.

## Proxies

gumtree.com.au sits behind bot mitigation (Peakhour). During development (2026-09-11, Sydney residential IP) it refused every automated browser: headless and headed Chromium, Firefox, and fingerprint-spoofed Chromium all received HTTP 403 (an empty body, a JavaScript challenge, or an "Access denied" page), while a regular Chrome window on the same connection loaded the pages normally. The default input therefore uses **Apify residential proxies, country AU**, and the crawler rotates its proxy session whenever a page comes back blocked. Running without a proxy is supported but is only expected to work from connections Gumtree already trusts.

## Performance and cost

One page load per 24 listings; a 100-listing run is 5 result pages. `includeDescription` adds one page per listing. Concurrency is kept low (1-2 pages at a time) with a randomised pause between loads to stay under Gumtree's rate limits; raise `requestDelaySecs` if you see 429s.

## FAQ

**Can it search all of Australia?** Yes, leave `location` empty or use a URL without an `l<id>` suffix.

**How do I find a category or location id?** Open the category or region on gumtree.com.au and read the URL: `/s-components/c18552` (category 18552), `/s-sydney/l3003435` (location 3003435).

**Why is `price` null?** The ad has no numeric price: Swap/Trade, free, or "price not listed". `priceText` and `priceType` say which.

**Does it get phone numbers or seller names?** No. It reads what is on the public listing card, plus the public description and condition when `includeDescription` is on. The seller's name, profile, phone and exact coordinates are never extracted.

**Why did my run fail with "0 listings scraped"?** Every page was blocked or unparseable. Check the proxy setting (residential, AU) first; if the selectors changed on Gumtree's side the log will say "no listing matched the selectors".

**Gumtree UK / NZ / South Africa?** Not supported; those are different sites with different markup.

## Legal

This actor reads publicly visible listing pages only, the same content anyone sees in a browser, and it does not log in or bypass any access control. It does not collect personal data beyond what Gumtree itself displays on a listing card (suburb, posting date, price). You are responsible for using the data in line with Gumtree's Terms of Use, its robots.txt, Australian privacy law and any applicable law in your jurisdiction; do not use it to contact people outside Gumtree's own messaging, and do not republish listing text or photos. Keep request rates modest.

Maintained by [tidesignal](https://github.com/tidesignal). Issues and selector updates: GitHub.
