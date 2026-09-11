# Store listing SEO notes

What buyers type when they look for a Gumtree scraper, and what the listing copy
targets because of it. Research done 2026-09-12. Scope: the listing page only
(title, description, README headings, FAQ). No other marketing.

## Sources

1. `https://api.apify.com/v2/store?search=gumtree&limit=50` (46 of 126 matching
   actors returned; title, description, categories, `currentPricingInfo`, users).
2. The three leader pages, fetched as HTML: `<title>`, `<meta name="description">`,
   README headings.
   - `apify.com/memo23/gumtree-cheerio` (216 users, 12,139 runs/30d)
   - `apify.com/sync-network/gumtree-com-listing-scraper` (119 users, 98,790 runs/30d, UK)
   - `apify.com/abotapi/gumtree-au-scraper` (4 users, 127 runs/30d, the only other AU-only actor)
3. Autocomplete, `hl=en&gl=au` / `market=en-AU`, for 32 seed phrases:
   Google (`suggestqueries.google.com/complete/search?client=firefox`),
   Bing (`api.bing.com/osjson.aspx`), DuckDuckGo (`duckduckgo.com/ac/`).

## What the Store itself shows

Titles: 30 of 46 actors are literally "Gumtree Scraper" or "Gumtree <X> Scraper".
The leaders add a hook after a dash or in brackets:

| Actor | Store title | `<title>` (SEO title) | Meta description (first 160) | Categories |
| --- | --- | --- | --- | --- |
| memo23/gumtree-cheerio | `Gumtree $1.75💰 [AUS-UK-NZ-SA] Search By URL or Keywords` | `Gumtree Scraper — Search By URL or Keywords` | "...VIN, mileage, condition), electronics details, and furniture dimensions. Get rich media (images, videos), location data, and competitor analysis." (the meta is a truncated tail of the description) | AUTOMATION, ECOMMERCE, LEAD_GENERATION |
| sync-network/gumtree-com-listing-scraper | `Gumtree.com Listing Scraper` | `Gumtree Scraper — Scrape UK Classified Listings` | "Scrape Gumtree UK classified listings. Search by keyword, filter by price and location. Extract title, price, description, images, seller info. Fast Che..." | AUTOMATION, ECOMMERCE |
| abotapi/gumtree-au-scraper | `Gumtree AU Scraper - Listings, Sellers & Reviews Scraper` | same | "Scrape gumtree.com.au classifieds across every vertical: for-sale goods, motors, real estate, jobs and services. Get title, description, price, all phot..." | ECOMMERCE, AUTOMATION, DEVELOPER_TOOLS |

README headings the leaders use (Apify renders README H2s into the page, so they are
indexable): memo23 "How it works / Features / How to Use / Input Data / Output
Structure / Recent Enhancements / Support / For AI Agents & LLM Apps / Disclaimer /
SEO Keywords"; sync-network "Features / Input Parameters / Output Example / Use Cases
/ Why Choose This Scraper?"; abotapi "What you get / Input / Example output / Notes /
Verification".

Words that recur in the top descriptions and are therefore what Store search matches
on: `scrape`, `listings`, `classifieds`, `title, price, location, description,
images`, `seller`, `keyword`, `category`, `URL`, `market research`, `price
tracking`, `lead generation`, `JSON / CSV / Excel`, `pay per result`.

Category counts across the 46 actors: ECOMMERCE 33, AUTOMATION 25,
LEAD_GENERATION 20, REAL_ESTATE 11, DEVELOPER_TOOLS 9. Both leaders with >100 users
are in AUTOMATION + ECOMMERCE. REAL_ESTATE is used by the actors that expose
property attributes (beds/baths); this actor has none, so it is not claimed.

Pricing across the Store is in `PUBLISH.md` section 4 (all pay-per-event; the two
actors above 100 users charge $1.50-$1.75 per 1,000 results plus $0.007-$0.01 per
start).

## What autocomplete shows

Only phrases that actually came back are listed; an empty list means none of the
three engines completes the phrase, i.e. negligible search volume.

| Seed | Google (AU) | Bing (en-AU) |
| --- | --- | --- |
| gumtree scraper | gumtree scraper (then dental "gum scraping" noise) | none |
| scrape gumtree | how to scrape gumtree | none |
| how to scrape gumtree | how to scrape gumtree, how to remove gumtree ad... | none relevant |
| gumtree api | gumtree api, gumtree api error 2, gumtree developer api | gumtree api, gumtree api error -2, gumtree app... |
| gumtree data | gumtree data entry jobs, gumtree data breach, gumtree data engineer, gumtree data | gumtree dating australia... |
| gumtree listings | gumtree listings, gumtree my listings, gumtree sold listings, old gumtree listings, gumtree previous listings, gumtree job listings, gumtree car listings | gumtree listings, gumtree listing fees, gumtree listing fee australia |
| gumtree price data | gumtree price history | none |
| gumtree price tracker | gumtree price history | none |
| gumtree alerts | gumtree alerts, gumtree search alerts | none |
| gumtree australia | gumtree australia, ...login, ...for sale, ...nsw, ...sydney, ...cars | gumtree australia, ...free local classifieds, ...perth, ...jobs |
| gumtree au | gumtree australia, gumtree aus | gumtree au, gumtree australia |
| gumtree automation | gumtree automation | none (automatic cars) |
| gumtree australia csv, gumtree listings export, gumtree csv, gumtree json, gumtree to excel, gumtree to csv, export gumtree | none | none |
| gumtree australia scraper, gumtree scraper python, gumtree web scraping, gumtree.com.au scraper, gumtree dataset, gumtree crawler, classifieds scraper australia | none | none |

Readings:

- "gumtree scraper" is the one tool-intent phrase Google completes. Everything with
  "scraper" in it is low-volume, but it is what the Store's own search box gets typed
  into, and 30 of 46 competitors carry it. It stays first in the title.
- "gumtree api" has real volume, mostly the app error message, but "gumtree developer
  api" shows developers looking for a programmatic way in. There is no public Gumtree
  API; the FAQ answers that search directly.
- "gumtree listings" plus "sold / old / previous listings" is people wanting listing
  data over time. That maps onto the scheduled-run / price-series use case, so the
  README says "listings" in the H1 and the use-case section talks about history.
- "gumtree price history" completes from two different seeds. Nobody has that data
  publicly; a daily run of this actor builds it. Named in the use cases and FAQ.
- "gumtree australia" dominates in volume and is the qualifier that separates this
  actor from the 40 UK ones; "gumtree.com.au" is how the AU competitor writes it in
  its description. Both forms are used.
- "csv / json / excel" get no autocomplete at all, but every leading description
  lists them and the Store search matches descriptions, so they are in the
  description and one FAQ, not in the title.
- "how to scrape gumtree" is a real Google phrase; used verbatim as an FAQ heading.
- Not targeted: "gumtree bot", "gumtree automation" (Google completes it but the
  intent is unclear and the word invites the wrong reading), anything UK/NZ/ZA, and
  "sellers / phone numbers / reviews" (abotapi's hook); this actor does not extract
  seller identity and the copy must not imply it.

## The six buyer terms the listing targets

1. `gumtree scraper` - title, H1, description
2. `gumtree australia` / `gumtree.com.au` - title, H1, description, FAQ
3. `gumtree listings` (incl. "old / previous listings") - title, H1, use cases
4. `gumtree api` - FAQ ("Is there a Gumtree API?")
5. `gumtree price history` / price tracking - use cases, FAQ
6. `how to scrape gumtree` - FAQ heading, "How it works"

Supporting words carried in the description because Store search matches on them:
`CSV`, `JSON`, `Excel`, `price`, `location`, `classifieds`, `pay per result`.

## What was chosen

- `actor.json` title (57 chars): `Gumtree Australia Scraper – Listings & Prices to CSV/JSON`
- `actor.json` description (150 chars): `Scrape gumtree.com.au listings to CSV/JSON/Excel: title, numeric price, suburb and state, posting date, image, Wanted/free/swap flags. Australia only.`
- Categories: E-commerce, Automation (what both >100-user leaders use; Real estate
  rejected, see above).
- README H1 carries the primary term and the qualifier; H2s are the phrases people
  search ("How to scrape Gumtree", FAQ questions in search form).

Nothing in the copy claims a field, a country or a feature the code does not have.
When the parser or fetch path changes, re-check the "Why this one" section and the
FAQ against `src/parser.py` and `src/main.py` before the next Store push.
