# Gumtree Australia Scraper: gumtree.com.au listings, prices and locations to CSV/JSON

Turn any **gumtree.com.au** search, category or location page into a dataset you can download as CSV, JSON or Excel: one row per listing with the title, the price as a number, suburb and state, posting date, image, category and the flags you need before you treat a classified ad as a price (Wanted, free, swap, faulty, bundle, promoted). Paste a Gumtree URL or give a keyword, set how many listings you want, run.

Australia only (gumtree.com.au). Not affiliated with Gumtree.

## Why this one

There are about forty Gumtree scrapers in the Store; nearly all of them are built for the UK site. This one differs in ways you can check in the code:

- **Australia only, on purpose.** gumtree.com.au has different markup, categories and bot mitigation from gumtree.com (UK). The input validator rejects non-`.com.au` URLs instead of producing empty or wrong rows.
- **Reads the page's own JSON, not the markup.** Gumtree renders result pages from `window.APP_DATA`; the scraper parses that first, then the `__NEXT_DATA__` blob of the Next.js variant Gumtree A/B-serves, and only falls back to the HTML cards if neither is present. Fewer things break when the CSS changes.
- **Gumtree's Peakhour challenge is handled.** A plain HTTP client with a real browser's TLS fingerprint tries first (cheap, no Chromium); if Gumtree answers with its JavaScript challenge, headless Chromium solves it and carries on. Through Apify residential AU proxies that is the normal path, and the log tells you which one ran.
- **Wanted / swap / free / faulty / bundle classification.** A "Wanted: RTX 4070, $4,000" ad, a "parts only" card and a "CPU + board + RAM combo" all have a price and all match your search; none of them tells you what the item sells for. Each row gets `kind` and `priceable` so you can filter in one step.
- **No silent empty runs.** If every page was blocked or nothing could be parsed, the run fails and says why. Only Gumtree's own "0 results" finishes with an empty dataset. A diffing job downstream never sees "everything sold" by mistake.
- **Numeric price, ISO timestamp, split location.** `price` is a number in AUD, `postedAtIso` is a Sydney-time timestamp derived from "8 hours ago", `suburb` / `area` / `state` are separate columns.

What it does **not** do: seller names, phone numbers, seller ratings or GPS coordinates. It reads the public listing card and, optionally, the public description page.

## Output fields

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
- **fetchMode**: `auto` (default), `http` or `browser`. See "How it works" below.
- **proxyConfiguration**: Apify residential proxies, country AU, is the tested setting.
- **httpProfiles** (advanced): override the ladder of browser fingerprints the HTTP client impersonates, e.g. `["safari18_0", "chrome124/http1.1"]`.

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

Download the dataset from the run's **Storage** tab as CSV, JSON, Excel or XML, or pull it through the API.

## Pricing

Pay per result: **$1.50 per 1,000 listings** plus **$0.01 per run start**, platform usage included (see the Pricing tab for the current figures). A default first run (24 listings) costs a few cents. `includeDescription` is not charged extra, but it loads one page per listing, so it is slower. To cap spend, set *Run options -> Max cost per run* before starting.

## Use cases

- **Gumtree price history.** Gumtree does not show what an item used to list for. Run the same search on a schedule, keep the `priceable` rows, and you have a real asking-price series for a GPU, a bike, a fridge. Listings that stop appearing are your best proxy for what sold.
- **Resale sourcing.** Sort by `price_asc`, filter `kind == "item"` and `condition`, compare against retail.
- **Local leads for movers, cleaners and tradies.** "moving sale", "must go this weekend", furniture in a suburb: `location` and `postedAtIso` let you act on fresh ads only. Contact people only through Gumtree itself.
- **Second-hand market research.** Category-wide pulls with `isPromoted`, `sellerType`, `state`.
- **Demand side.** `adType: "wanted"` gives you what people are trying to buy and what they offer.

## How to scrape Gumtree: how it works

By default no browser is involved. A plain HTTP client (`curl_cffi`) that presents the TLS and HTTP/2 fingerprint of a real browser fetches each result page and reads the JSON the page is rendered from (`window.APP_DATA.search.results`). Gumtree also A/B-serves a Next.js implementation of the same page (`__NEXT_DATA__`, 40 listings per page); that is read too. If neither blob is present, the rendered cards are parsed. Pagination follows Gumtree's own next-page link until `maxItems` is reached or the last page is hit. Rows are de-duplicated by listing id across pages and start URLs. One HTTP session (cookie jar, proxy session, fingerprint profile) is kept for the whole run and rotated only when Gumtree blocks it.

### Fetch modes

| `fetchMode` | What happens | When to use |
| --- | --- | --- |
| `auto` (default) | One plain-HTTP attempt. If Gumtree answers with its JavaScript challenge, headless Chromium loads that same page, solves the challenge and parses it; the browser's cookies are offered to the HTTP client once, and if Gumtree challenges them again (it does, see below) the rest of the run stays in the browser. | Always, unless you are debugging. |
| `http` | HTTP client only. No Chromium: a page takes well under a second and a few hundred KB. Fails on pages that demand the challenge. | Connections Gumtree trusts (a home line in Australia); scheduled runs where you know HTTP works. |
| `browser` | Headless Chromium (Playwright) for every page. A new session is challenged once (about a second); about 1 GB of memory. | Through proxies; or if `http` stops working after a change on Gumtree's side. |

### Gumtree's bot mitigation

gumtree.com.au sits behind Peakhour. What it answers depends on the client fingerprint and the IP reputation (measured 2026-09-12):

- From a Sydney residential IP, `curl_cffi` requests impersonating Safari 18 / Chrome 124 / Chrome 120 get the page straight away; the newest Chrome profiles and headless Chromium are refused. The HTTP client therefore tries a ladder of profiles (`safari18_0` over HTTP/2, `chrome124` over HTTP/1.1, `chrome120`, `safari15_5`), rotating profile, cookie jar and proxy session whenever a response is a challenge (`peakhour-challenge: 1`), a hard block (`peakhour-error: blocked`) or a 429.
- Through Apify residential proxies (country AU), every one of those profiles gets the **JavaScript challenge**: a 31 KB page that computes a proof-of-work, fingerprints the browser and POSTs the answer back. Only a real browser can answer it. Headless Chromium does, in 0.6-1 s, provided its client hints match its binary (the scraper overrides the `HeadlessChrome` marker via CDP). A 403 is therefore not treated as a failure in the browser: the page is given up to 20 s to turn into real content, then judged.
- The cookie the browser earns (`__rp_ch`) is bound to the TLS fingerprint that solved the challenge: handing it to `curl_cffi` was challenged again on the next request in every test. Inside the browser it carries: page 2 and listing pages of the same session load without a new challenge.

Practical consequence: **on the Apify platform expect the browser to do the work** (about a second per page plus Chromium's memory); from a trusted connection the HTTP path does it for a fraction of the cost. The log says which happened (`Page 1 (app_data, http safari18_0/h2 ...)` vs `Peakhour challenge solved in the browser` and `Page 1 (app_data, browser, HTTP 403) ...`).

### Selectors and data sources

Verified 2026-09-11 against live pages ("rtx 4070", "exercise bike" in Sydney Region, the Components category, a Wanted-only category page):

- Listing card: `a.user-ad-row-new-design` (`id="user-ad-<id>"`, `href="/web/listing/<category>/<id>"`)
- Title `.user-ad-row-new-design__title-span`, price `.user-ad-price-new-design__price` (also shows `Swap/Trade`), negotiable badge `.user-ad-price-new-design__negotiable-label`, location `.user-ad-row-new-design__location`, date `.user-ad-row-new-design__age` ("8 hours ago", "Yesterday", "09/09/2026"), snippet `.user-ad-row-new-design__description-text`
- Sponsored blocks: `div.fuse-ads` siblings between cards (never matched by the card selector)
- Next page: `a.page-number-navigation__link-next`; result count `h1.breadcrumbs__summary--enhanced`
- Listing page (`includeDescription`): JSON-LD `Product` block and `__NEXT_DATA__` -> `props.pageProps.vipData.data`
- URL grammar: `/s-<keyword>/k0`, `/s-<slug>/c<categoryId>`, `/s-<slug>/l<locationId>`, `/s-<slug>/<slug>/<keyword>/k0c<cat>l<loc>`, `?sort=date|rank|price_asc|price_desc`, `?ad=offering|wanted`, `?price-type=free`

### Empty-result guard

The scraper refuses to finish successfully with an empty dataset. If pages load but no listing can be parsed, or every request was blocked, the run fails with a message saying which. A genuine "0 results" search (Gumtree's own `zeroSearchResults`) finishes normally with no rows.

## Proxies

The default input uses **Apify residential proxies, country AU**; a fresh proxy session is taken whenever a page comes back blocked. Datacenter proxies and non-AU exits are more likely to be refused. Running with no proxy works from a residential Australian connection (that is how the scraper was developed). Pages are fetched one at a time with a randomised pause (`requestDelaySecs`, default 2 s); raise it if you see 429s.

## FAQ

**Can I export Gumtree listings to CSV?** Yes. Every run writes to an Apify dataset, which you download from the run's Storage tab as CSV, JSON, Excel, XML or HTML, or fetch by API (`/v2/datasets/<id>/items?format=csv`). Column names are the field names in the table above.

**Does it work for gumtree.com.au only?** Yes. Gumtree UK (gumtree.com), New Zealand and South Africa are different sites with different markup and are rejected by the input validator rather than half-supported. All of Australia is covered: leave `location` empty or use a URL without an `l<id>` suffix.

**How much does it cost per 1,000 listings?** $1.50 in result fees plus $0.01 per run start, so about $1.51 for a single 1,000-listing run; platform compute and proxy traffic are included in that price. With `includeDescription` on, the price is the same but the run takes roughly 25 times longer because each listing page is loaded.

**Is there a Gumtree API?** Not a public one. Gumtree Australia has no developer API; this actor is the programmatic route: call it through the Apify API (`POST /v2/acts/tidesignal~gumtree-au-scraper/runs`), through the Apify clients for Python and JavaScript, on a schedule, or as an MCP tool.

**Is scraping Gumtree legal?** Not legal advice. The actor reads only public listing pages, the same content anyone sees without an account; it does not log in and never extracts seller names, phone numbers or exact coordinates, only what the public card shows (suburb, price, posting date). Collecting public facts is treated differently from copying protected content or personal data, so keep listing text and photos out of anything you republish, do not contact people outside Gumtree's own messaging, and keep request rates modest. Gumtree's Terms of Use restrict automated access; you are responsible for your use of the data under those terms, Australian privacy law and the law where you are.

**How often does it break?** No promise can be made about a site that changes without notice. Gumtree currently serves two page implementations at once; the parser reads the JSON both are rendered from, so CSS changes alone do not touch it. The other moving part is Peakhour's fingerprint database: when a profile on the HTTP ladder stops being trusted the browser path takes over. When something does break, the run fails with a clear message instead of returning an empty dataset, so a scheduled run of yours tells you the same day. Selector updates are pushed to this repository; report a failing search URL via the Issues tab.

**Why is `price` null?** The ad has no numeric price: Swap/Trade, free, or "price not listed". `priceText` and `priceType` say which.

**Why did my run fail with "0 listings scraped"?** Every page was blocked or unparseable; the log says which. "blocked response(s) from bot mitigation" means the HTTP profiles were refused and the browser could not solve the challenge either: check the proxy setting (residential, AU) first. "No listing matched the selectors" means Gumtree changed its page and the parser needs an update; open an issue with the search URL.

## Legal

This actor reads publicly visible listing pages only, the same content anyone sees in a browser, and it does not log in. It does not collect personal data beyond what Gumtree itself displays on a listing card (suburb, posting date, price). You are responsible for using the data in line with Gumtree's Terms of Use, its robots.txt, Australian privacy law and any applicable law in your jurisdiction; do not use it to contact people outside Gumtree's own messaging, and do not republish listing text or photos. Keep request rates modest. Not affiliated with Gumtree.

## Changelog

- **0.4** (2026-09-12): the browser waits for the post-challenge document to finish parsing before reading it (fixes half-rendered pages); run usage is logged at the end of each run.
- **0.3** (2026-09-12): Peakhour JavaScript challenge solved in headless Chromium; `auto` mode = one HTTP attempt, browser solves the challenge, browser cookies offered to the HTTP client once, run stays in the browser if refused.
- **0.2** (2026-09-12): HTTP-first fetch path (`curl_cffi` browser fingerprint impersonation, profile ladder); `fetchMode` input; parser for Gumtree's Next.js search-page variant; `maxItems` default lowered to 24.
- **0.1** (2026-09-11): first release. Playwright/Crawlee crawler, `APP_DATA` parser with tests on real pages, `kind` / `priceable` classification, empty-result guard.

Maintained by [tidesignal](https://github.com/tidesignal). Issues and selector updates: [GitHub](https://github.com/tidesignal/gumtree-au-scraper/issues).

## 中文简介

Gumtree Australia Scraper 只抓取澳大利亚站 gumtree.com.au（不支持英国、新西兰、南非站）。粘贴任意 Gumtree 搜索、分类或地区页面的网址，或者直接给关键词，每条广告输出一行：标题、数字价格（澳元）、区域和州、发布时间（换算成悉尼时间的 ISO 时间戳）、图片、分类，以及 Wanted（求购）/ 免费 / 交换 / 故障件 / 打包出售等标记（`kind` 和 `priceable` 字段），方便直接过滤出真正可当作成交参考的报价。解析优先读取页面自带的 JSON（`window.APP_DATA` 和 `__NEXT_DATA__`），页面样式改动一般不影响；Gumtree 的 Peakhour 人机验证由无头 Chromium 自动完成。如果所有页面都被拦截或解析不到任何广告，运行会直接报错而不是返回空数据集。结果可在 Apify 控制台导出为 CSV、JSON 或 Excel，也可以通过 API 或定时任务调用。按结果计费：每 1,000 条 1.50 美元，另加每次启动 0.01 美元。不抓取卖家姓名、电话或精确坐标；请遵守 Gumtree 使用条款和当地法律，不要转发广告文字和图片。
