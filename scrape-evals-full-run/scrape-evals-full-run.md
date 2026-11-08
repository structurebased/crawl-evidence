# scrape-evals full-corpus run: /crawl against their committed table

**Verdict:** `/crawl` on all 1,000 URLs: **67.0% coverage, 0.386 F1**. That is **4th of 14 by coverage** and **12th of 14 by F1** against the fork's committed full-corpus table. The gap to their top rows is F1, not fetch rate: /crawl covers as many URLs as their `tavily_api` row but scores them worse.

| Rank (coverage) | Engine | Coverage | F1 |
|---: | --- | ---: | ---: |
| 1 | firecrawl_api (theirs) | 80.9% | 0.684 |
| 2 | exa_api (theirs) | 76.3% | 0.534 |
| 3 | tavily_api (theirs) | 67.6% | 0.503 |
| **4** | **hermes_crawl /crawl (this run)** | **67.0%** | **0.386** |
| 5 | scraperapi_api (theirs) | 63.5% | 0.448 |
| 6 | zyte_api (theirs) | 62.9% | 0.466 |
| 7 | scrapingbee_api (theirs) | 60.6% | 0.451 |
| 8 | apify_api (theirs) | 60.2% | 0.418 |
| 9 | crawl4ai_scraper (theirs) | 58.0% | 0.453 |
| 10 | selenium_scraper (theirs) | 55.0% | 0.404 |
| 11 | scrapy_scraper (theirs) | 54.0% | 0.426 |
| 12 | puppeteer_scraper (theirs) | 53.7% | 0.410 |
| 13 | rest_scraper (theirs) | 50.6% | 0.355 |
| 14 | playwright_scraper (theirs) | 39.5% | 0.344 |

Sorted by F1 instead, /crawl sits between `selenium_scraper` (0.404) and `rest_scraper` (0.355), rank 12.

## What was run

Dataset `datasets/1-0-0.csv`, all 1,000 rows. 135 rows have both `truth_text` and `lie_text` empty (recorded fetch errors), so the ceiling on coverage is 86.5%.

| Engine | Rows | Coverage | F1 | Recall | Precision | Wall | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| hermes_crawl (/crawl) | 1000 | 0.670 | 0.386 | 0.377 | 0.412 | 66 min | 4 workers, 75s adapter timeout |
| crawl4ai_scraper | 1000 | 0.485 | 0.356 | 0.348 | 0.373 | 51 min | fresh browser per URL, sequential |
| rest_scraper | 1000 | 0.449 | 0.289 | 0.281 | 0.305 | 50 min | sync, sequential |

- Scoring: the fork's `evals/analysis/quality_analyzer.py`, same scorer as the slice. Each URL was re-scored fresh after the run; every stored `grader_output.json` matched the fresh result to 1e-12 (`graders_match_fresh`, 0 mismatches), and the recompute matched the harness summary to 1e-9.
- `playwright_scraper` skipped honestly: 30 s/URL timeout plus a fresh browser per URL puts it past 8 h; the fork's committed 39.5%/0.344 row is already its worst.
- `rest_scraper` and `crawl4ai_scraper` ran concurrently from the same egress (two outbound streams). /crawl ran alone.
- All three runs used the fork's own `run_harness.py --suite quality`, `--resume-safe`, with `CRAWL_ENSEMBLE_CACHE=0` and `CRAWL_CAP_CACHE=0`, robots.txt respected.

## /crawl vs every committed row

Same two /crawl numbers, compared to each row (delta = ours minus theirs):

| Engine | Their coverage | Their F1 | Our coverage | Our F1 | Coverage delta | F1 delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| firecrawl_api | 80.9% | 0.684 | 67.0% | 0.386 | −13.9 | −0.298 |
| exa_api | 76.3% | 0.534 | 67.0% | 0.386 | −9.3 | −0.148 |
| tavily_api | 67.6% | 0.503 | 67.0% | 0.386 | −0.6 | −0.117 |
| scraperapi_api | 63.5% | 0.448 | 67.0% | 0.386 | +3.5 | −0.062 |
| zyte_api | 62.9% | 0.466 | 67.0% | 0.386 | +4.1 | −0.080 |
| scrapingbee_api | 60.6% | 0.451 | 67.0% | 0.386 | +6.4 | −0.065 |
| apify_api | 60.2% | 0.418 | 67.0% | 0.386 | +6.8 | −0.032 |
| crawl4ai_scraper | 58.0% | 0.453 | 67.0% | 0.386 | +9.0 | −0.067 |
| selenium_scraper | 55.0% | 0.404 | 67.0% | 0.386 | +12.0 | −0.018 |
| scrapy_scraper | 54.0% | 0.426 | 67.0% | 0.386 | +13.0 | −0.040 |
| puppeteer_scraper | 53.7% | 0.410 | 67.0% | 0.386 | +13.3 | −0.024 |
| rest_scraper | 50.6% | 0.355 | 67.0% | 0.386 | +16.4 | +0.031 |
| playwright_scraper | 39.5% | 0.344 | 67.0% | 0.386 | +27.5 | +0.042 |

/crawl covers more than every paid scraping proxy row and the two local browser engines; it loses on F1 to all of them except `rest_scraper` and `playwright_scraper`.

## Free local engines, re-run on our egress

The two free engines we could also run, against the fork's committed rows for the same engines:

| Engine | Their coverage | Their F1 | Our coverage | Our F1 | Coverage delta | F1 delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| crawl4ai_scraper | 58.0% | 0.453 | 48.5% | 0.356 | −9.5 | −0.097 |
| rest_scraper | 50.6% | 0.355 | 44.9% | 0.289 | −5.7 | −0.066 |

Both dropped against our egress. /crawl did not drop as far: 67.0% coverage here against `tavily_api`'s 67.6% and `scraperapi_api`'s 63.5% on their infra.

## /crawl per-category breakdown

The CSV carries no explicit category column; this is the `page_type` bucket their own analyzer derives.

| Page type | Tasks | Frozen | Coverage | F1 |
| --- | ---: | ---: | ---: | ---: |
| static | 800 | 107 | 69.4% | 0.388 |
| article | 70 | 6 | 68.6% | 0.460 |
| documentation | 61 | 6 | 68.9% | 0.490 |
| unknown | 28 | 8 | 0.0% | 0.000 |
| pdf | 17 | 4 | 41.2% | 0.349 |
| ecommerce | 17 | 1 | 82.4% | 0.357 |
| structured | 2 | 1 | 50.0% | 0.080 |
| github | 1 | 0 | 100% | 0.685 |
| youtube | 1 | 0 | 100% | 0.387 |
| pricing | 1 | 0 | 100% | 0.711 |
| document / invalid | 2 | 2 | 0% | 0.000 |

Articles and documentation score best (0.46, 0.49); PDFs are the weak winnable class at 41.2% coverage; the `unknown` bucket is all-fetch-failures.

## /crawl failure taxonomy

330 of 1,000 rows did not count as covered:

| Class | Count | Share of 330 |
| --- | ---: | ---: |
| frozen dataset rows (no truth/lie) | 135 | 40.9% |
| HTTP 403 | 63 | 19.1% |
| HTTP 404 | 50 | 15.2% |
| HTTP 5xx (45 × 500, 1 × 503, others) | 46 | 13.9% |
| adapter timeout (75s) | 20 | 6.1% |
| 2xx/3xx fetched but no truth token found | 9 | 2.7% |
| HTTP 429 / 401 / other | 7 | 2.1% |

Covered-but-weak (counts toward coverage, drags F1): 106 pages with F1 < 0.15. **97 are shells**: the adapter extracted tiny visible text from a large HTML payload (`raw_html_len > 4 × text_len`, or the page said "requires javascript"). By page type the shells are 92 static, 6 ecommerce, 4 article, 3 documentation, 1 pdf.

The 63 403s spread across 63 distinct hosts (no single-host spike); only 2 HTTP 429s, so rate limiting was not the driver for /crawl.

Winnable rows only (excluding the 135 frozen): /crawl coverage 77.5%, F1 0.447. Covered pages only (670): F1 0.576, truth-token recall median 0.685. If the 106 weak covered pages merely reached the covered median (0.652), average F1 would rise from 0.386 to 0.449.

## Caveats

- **Egress.** This run went out from a Morocco residential connection. Their table was produced on their own infra. Every absolute number above is not a head-to-head; the delta columns are an egress effect plus a corpus-age effect, not a clean engine comparison.
- **Corpus age.** Their run date is unknown; the 135 frozen rows and the 50 fresh 404s mean the dataset has decayed since.
- **Concurrency.** `rest_scraper` and `crawl4ai_scraper` shared the egress (two streams); rest already shows 25 HTTP 429s. /crawl ran alone and saw 2.
- **Playwright skipped**, as noted. **Firecrawl/Exa/Tavily etc. are compared only from the committed rows**, no paid API calls were made.
- **/crawl adapter rename.** Mid-session a concurrent lane in the fork renamed `engines/hermes_crawl.py` to `engines/crawl_engine.py`; the completed run imported the original module and its 1,000 outputs are intact. The re-score imports only `evals.analysis.quality_analyzer`, so it does not depend on the engine module.
- **Launcher progress.** `crawl4ai_scraper` redirects stdout, so the launcher's printed-progress counter under-counted (progress file written `done=1`); the file was corrected by counting `scrape_output.json` files, which is authoritative.

## Verification

| Claim | Command | Exit | Evidence |
| --- | --- | ---: | --- |
| 1,000/1,000 scraped, /crawl | `run_full_engine.py --engine hermes_crawl ...` | 0 | summary line, 1000 `scrape_output.json`, `full-progress-hermes_crawl.json` |
| Scores are theirs | `full_analyze.py` (fresh `QualityAnalyzer.analyze_one` per URL) | {{ANALYZE_EXIT}} | 0 grader mismatches, summary matches harness to 1e-9 |
| rest / crawl4ai complete | same launcher, each engine | 0 | 1000 outputs + 1000 graders each, summary lines |
| Rank | `full_analyze.py` ranking block | - | `full_compare.json` `ranked_with_ours` |

## Artifacts

All under `eval/research/scrape-evals-artifacts/`:

- `full_analyze.py`, `run_full_engine.py`, `run_harness.py` — scoring, launcher, harness
- `full-hermes_crawl.log`, `full-rest_scraper.log`, `full-crawl4ai_scraper.log`, `full_analyze.log` — logs
- `full-progress-*.json` — per-URL progress, resumable
- `full-crawl_diag.jsonl` — 1,000 diagnostic records (page type, text/raw lengths, HTTP detail)
- `full_scores.csv` — 3,000 per-URL scores
- `full_compare.json` — summaries, committed reference, rank
- `full_taxonomy.json`, `full_pagetype.json` — failure and page-type breakdowns
- `full-<engine>_summary.json` — harness summary per engine
