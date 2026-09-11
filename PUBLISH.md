# Publishing to Apify Store (V0)

Internal checklist. Everything below is done in a browser, nothing needs to be scripted.

## 0. Before Console

1. Create the GitHub org `tidesignal` and the empty repo `github.com/tidesignal/gumtree-au-scraper` (public or private; Apify can read either once its GitHub app is authorised).
2. Push this repo: `git remote add origin git@github.com:tidesignal/gumtree-au-scraper.git && git push -u origin main`.
3. Apify account username must be `tidesignal` (Settings -> Account -> Username). The Store URL will be `apify.com/tidesignal/gumtree-au-scraper`.
4. Run the tests once more: `python -m pytest -q`.

## 1. Create the Actor from the repo

Console -> **Actors** -> **Development** -> **Create new** (button top right) -> choose **Link Git repository** (instead of a template).

- Git URL: `https://github.com/tidesignal/gumtree-au-scraper`
- Branch: `main`, build tag `latest` (defaults)
- Actor name is read from `.actor/actor.json` (`gumtree-au-scraper`), title "Gumtree Australia Scraper".
- If Console asks for a folder, leave it blank (the Dockerfile is at `.actor/Dockerfile`, which is the standard location).
- **Source** tab -> **Build**. The build installs `requirements.txt` on `apify/actor-python-playwright:3.13`. Expect 3-5 minutes the first time.
- Optional but recommended: Source -> "Automatic builds on push" (GitHub webhook) so `main` rebuilds itself.

## 2. Test run in Console

**Input** tab: leave the prefilled URL (`https://www.gumtree.com.au/s-rtx+4070/k0`), `maxItems` 24 (the default), `fetchMode` auto (the default), proxy = Apify Proxy, group RESIDENTIAL, country AU (the input default). Set *Run options -> Max cost per run* to `$0.10`. **Start**.

Pass criteria: run status SUCCEEDED, dataset has 24 rows. Through the proxy the expected log is: one `Blocked (challenge, HTTP 403 ...)` for the HTTP probe, then `solving the Peakhour challenge in the browser for that page`, `Peakhour challenge solved in the browser for ...`, `Page 1 (app_data, browser, HTTP 403) ...: 24 listings`, then `adopted from browser` / `Blocked (... adopted browser session ...)` / `staying in the browser` if there is a page 2. The summary line should say `N challenges solved, browser session refused over HTTP` and `0 half-rendered pages` (a non-zero count means a page was captured before it finished rendering, as in run e7Pbi0HTl8gKNCEwH, and the page readiness check in `src/main.py` needs a look). The line after it, `Run usage so far: compute ... CU, residential proxy ... MB, total $...`, is the number to price against. If instead the HTTP probe gets `Page 1 (app_data, http ...)`, the proxy IP was trusted and the run was even cheaper. Failure signatures: `challenge not solved` (the browser ran the challenge and Peakhour rejected the answer: the fingerprint consistency in `chromium_ua_override` needs a look) or `challenge not offered` (hard block, `peakhour-error: blocked`: the proxy IP or the Chromium TLS fingerprint is on a blocklist). See "Known risks".

Run once more with `includeDescription: true` and `maxItems: 5`; check `description`, `condition`, `postcode` are filled.

## 3. Publication tab

Actor -> **Publication** tab -> **Publish to Store**:

| Field | Value |
| --- | --- |
| Title | `Gumtree Australia Scraper` |
| SEO title (<= 60 chars) | `Gumtree Australia Scraper - listings, prices, locations` |
| SEO description (<= 160 chars) | `Scrape gumtree.com.au search and category pages into a dataset: title, price as a number, suburb, posting date, image, Wanted/free/swap flags. AU only.` |
| Description (short, Store card) | `Scrape gumtree.com.au search results and categories into a clean dataset: title, price, location, posting date, image, category, and Wanted / free / swap / promoted flags.` |
| Categories | `E-commerce` (primary), `Automation` |
| Icon | 512x512 PNG; a plain wave/tide mark on white. Not the Gumtree logo. |
| README | pulled from `README.md` in the repo; check the preview renders the tables |
| Repository URL | `https://github.com/tidesignal/gumtree-au-scraper` |
| Issues URL | `https://github.com/tidesignal/gumtree-au-scraper/issues` |
| License | MIT |

Actor description and README must not use the Gumtree logo or claim affiliation (README already says "Not affiliated with Gumtree").

## 4. Monetization (pricing)

Actor -> **Monetization** tab.

### What the competition charges (Apify Store API, 2026-09-11)

`curl "https://api.apify.com/v2/store?search=gumtree&limit=20"`, `currentPricingInfo` per actor. Every Gumtree actor in the Store is on **pay-per-event**; nobody uses rental or pay-per-result-only.

| Actor | Users | Runs (30d) | Result price | Actor start | Notes |
| --- | --- | --- | --- | --- | --- |
| `memo23/gumtree-cheerio` | 216 | 12,139 | **$0.00175** ($1.75 / 1k) | $0.007 | extra events: phone number $0.00275, monitoring $0.0005 |
| `sync-network/gumtree-com-listing-scraper` | 119 | 98,790 | **$0.0015** ($1.50 / 1k) | $0.01 | Gumtree UK |
| `crawlerbros/gumtree-scraper` | 55 | 713 | $0.005 FREE tier, $0.003 GOLD+ | $0.05 | platform usage paid by user |
| `abotapi/gumtree-au-scraper` | 4 | 127 | $0.0015 FREE tier, $0.001 PLATINUM+ | $0.08 FREE tier, $0.05 GOLD+ | the only other AU-specific actor |

Market norm: **$1.50-$1.75 per 1,000 results plus a cent or less per start.** The two actors above 100 users are both in that band; the two that charge $0.003-0.005 per result have 4-55 users.

### Recommended V0 pricing

Pay-per-event, two built-in events:

| Event | Price | Why |
| --- | --- | --- |
| Actor start | **$0.01** | same as sync-network; covers browser + proxy session start |
| Result (default dataset item) | **$0.0015** ($1.50 / 1k) | match the cheaper of the two market leaders; we have no track record yet and are AU-only |

Cost sanity check: a result page is ~0.5-1.5 MB through residential proxy (about $8/GB on Apify) = ~$0.004-0.012 per page = $0.0002-0.0005 per result, plus a few seconds of 1 GB browser compute. Margin at $0.0015 is thin but positive; Apify takes 20% of PPE revenue on top. Do not go lower.

`includeDescription` costs one extra page per row (25x the proxy traffic). V0 does not charge extra for it; if it gets used, add a custom event `listing-detail` at ~$0.003 and call `Actor.charge(event_name="listing-detail")` from `handle_listing` in `src/main.py`. Custom events are not billed without that call; the two built-in events (Actor start, dataset item) are charged by the platform automatically, verify that on the Monetization page when setting them up.

Do **not** tick "platform usage paid by user" for V0: it makes the run price unpredictable for the user, and the market leaders do not use it.

## 5. After publishing

- Store review takes up to a few days; the actor is visible at `apify.com/tidesignal/gumtree-au-scraper` once approved.
- Set up an Apify **monitoring** schedule: daily run, `maxItems` 24, alert on failure. The actor fails loudly when Gumtree changes markup or blocks the proxy, so a failed scheduled run is the signal to fix selectors in `src/parser.py` (the "How it works" section of the README lists them).
- Answer Issues on the Actor page within a day or two; Store ranking weighs responsiveness.

## Known risks

- **Bot mitigation.** Peakhour on gumtree.com.au fingerprints the client *and* scores the IP. Platform runs `OIa36sYyzTSm4rGLi` (headless Chromium, 403 as an error) and `LGxim3NgeVeAC3esm` (curl_cffi, every profile challenged) both failed through RESIDENTIAL/AU proxies. Build 3 lets the challenge run in headless Chromium (403 is not an error; client hints made consistent via CDP), which solves it locally in under a second; the same through the proxy is the untested piece, so step 2 is still the hard gate. If the platform browser gets `challenge not solved`, compare `chromium_ua_override` against the platform's Chromium version; if `challenge not offered`, the proxy IPs are being hard-blocked and Gumtree should be shelved as a V0 target. The HTTP ladder (`src/http_fetch.py`, `PROFILE_LADDER`) only matters from trusted connections.
- **Cost through the proxy.** Each page is a browser page load plus a challenge (about a second) and a Chromium process (1 GB); images/fonts/media are blocked. Budget roughly 3-5x the HTTP-path estimate in section 4 for platform runs, and revisit pricing after the first real runs.
- **Location ids.** Only Sydney Region (3003435) is aliased; everything else needs the numeric id. A follow-up could add the other capital-city region ids after reading them off real URLs.
- **UK/NZ/ZA** are out of scope and rejected by the input validator, which keeps the promise in the title honest.
