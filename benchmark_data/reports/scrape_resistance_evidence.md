# Environment realism: why agents cannot bulk-scrape the catalog (citation-backed)

Evidence that our Amazon-clone's anti-shortcut design (specs PDP-only + per-session distinct-PDP
budget + capped pagination) faithfully models real online retail. Verified against primary/authoritative
sources. Confidence: [PRIMARY]=official source, [VENDOR]=reputable industry source, [REPORTED]=widely
reported but not officially confirmed.

## Our design → real-world basis

| Our env mechanism | Real-world basis | Sources |
|---|---|---|
| **Specs only on the PDP** (search/listing cards carry title/price/rating/image/badges, no spec prose) | Listing pages show only a minimal summary; full specs live on the per-item detail page. Scraping tools split schemas: "search" target = summary + URL; "product" target = specs. | Oxylabs Amazon Search vs Product targets; NN/g e-commerce PDP guidance; WebScraper.io two-step crawl; Bright Data |
| **Per-session distinct-PDP budget (≈25); re-views free** | No free bulk catalog API + per-PDP cost + anti-scrape rate limits. PA-API 5.0: requires approved Associate (3 qualifying sales/180d), default **1 req/s & 8,640/day**, max **10 req/s only after ~$43,200 attributed revenue**, **≤100 results/query (10×10)**, access revoked after 30 days w/o sales, deprecating 2026. Naive scraper blocked after ~10–30 requests; residential proxies advise **3–5 req/min/IP, <100/day/IP**. | Amazon PA-API docs (api-rates, search-items); Associates help; BrowseAct; amazonscraperapi |
| **Capped pagination (24/page, ~7 pages)** | Amazon search caps reachable depth (~7 pages observed; Amazon frames the tail as "search suppression"); cross-platform: eBay 10,000 items, Google ~1,000. | ScrapeHero; Amazon Seller Forums; eBay developer docs [PRIMARY]; Google |
| **Browser agent has no privileged channel** | LLM browser agents (WebVoyager, browser-use) act on the *rendered* page via Selenium screenshots + human-like clicks — same PDP-only/pagination/rate-limits as any browser; headless automation is *detectable* (navigator.webdriver, HeadlessChrome token, CDP artifacts) and often blocked faster. | WebVoyager (arXiv 2401.13919, evaluated on Amazon); Castle; DataDome; browser-use's own bot-detection post |

## Key primary facts (quote without hedging)
- **PA-API 5.0 rate limits** — "one request per second … 8640 requests per day" initially; scales "one TPS … up to a maximum of ten TPS for every $4320 of shipped item revenue"; "429 TooManyRequests" on throttle; access lost after a "consecutive 30-day period" without qualifying sales. (webservices.amazon.com/paapi5/documentation/troubleshooting/api-rates.html)
- **PA-API result cap** — SearchItems ItemCount 1–10, ItemPage 1–10 ⇒ **100 items/query max**; GetItems needs ASINs you already know. (…/search-items.html)
- **Amazon Conditions of Use** prohibit "any use of data mining, robots, or similar data gathering and extraction tools" and creating a database of "substantial parts" (e.g. prices). (amazon.com/gp/help/customer/display.html?nodeId=GLSBYFE9MGKKQXXM)
- **eBay** caps search at 10,000 items (100 pages × 100). (developer.ebay.com/support/kb-article?KBid=2106)
- **WebVoyager** "automated web-browsing environment using Selenium," "screenshots as the primary source of input," acts via Click/Input/Scroll — evaluated on real sites incl. Amazon. (arXiv 2401.13919)
- **Amazon v. Perplexity (Nov 2025)** — Amazon's cease-and-desist + lawsuit against the "Comet" AI shopping agent alleges it "disguises… automated activity as if it were human shopping," breaching the data-mining/robots ban — direct evidence that automated AI-agent harvesting is treated as a ToS violation, not a sanctioned capability.

## Hedge on these two (use "in practice"/"reportedly")
- Amazon **~7-page** search cap is heavily observed/vendor-stated but NOT officially published (Amazon attributes it to result suppression). [REPORTED/observational]
- A **10-sales/30-day** PA-API maintenance rule is reported, not official; the **3-sales/180-day** entry rule IS official. [REPORTED vs PRIMARY]

## Counter-evidence, and why it doesn't undermine the design
Commercial services DO scrape Amazon at 85–99% success — but only with rotating residential/mobile
proxy pools + paid CAPTCHA solvers + real-browser/stealth stacks + self-throttling (3–5 req/min/IP).
A single in-context shopping agent (1) cannot provision that infrastructure mid-task; (2) has low naked
success (requests-only ≈10%; blocked after 10–30 items); (3) is acting adversarially against ToS (cf.
Amazon v. Perplexity); and (4) even funded pipelines still pay the per-PDP two-step cost. So heavy-infra
scraping confirms — rather than refutes — that catalog harvesting is costly/adversarial/infrastructure-
dependent, not a free shortcut. Our no-bulk-shortcut design is a faithful, defensible abstraction.
