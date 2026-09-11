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

**Input** tab: leave the prefilled URL (`https://www.gumtree.com.au/s-rtx+4070/k0`), `maxItems` 30, proxy = Apify Proxy, group RESIDENTIAL, country AU (that is the input default). **Start**.

Pass criteria: run status SUCCEEDED, dataset has ~30 rows, log shows `Page 1 (app_data) ...: 24 listings`. If it fails with `0 listings scraped: N blocked response(s)`, the residential AU proxy is not getting through either; do not publish until it does (see "Known risks").

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

- **Bot mitigation.** Peakhour on gumtree.com.au refused every automated browser during local development (see README "Proxies"). Residential AU proxies are the tested plan but were not verified before this commit because no Apify proxy was available locally. Step 2 is therefore a hard gate.
- **Location ids.** Only Sydney Region (3003435) is aliased; everything else needs the numeric id. A follow-up could add the other capital-city region ids after reading them off real URLs.
- **UK/NZ/ZA** are out of scope and rejected by the input validator, which keeps the promise in the title honest.
