# Leaderboard verification: 2026 scraping report vs primary sources

Verified 2026-11-08 by cloning/fetching the primary repos, pages and raw result
files. The pasted report is treated as a claim, not evidence. Every verdict
below names the primary artifact and the command that produced it.

## Verdict table (one line per claim)

| # | Claim from the report | Verdict | Primary evidence |
| --- | --- | --- | --- |
| 1 | Nimble 90.2% completeness on 5,856 URLs / 2,107 domains, deterministic scoring | VERIFIED | llms.nimbleway.com/benchmarks: Nimble row 0.902 completeness, 99.1% fetch success, 85.9 composite; methodology: "graded locally and deterministically, no LLM judge" |
| 1b | Nimble extraction harness is open | REFUTED | Nimbleway/Public-benchmarks contains only DRACO, SimpleQA, Finance, GTM (GitHub API contents listing). No extraction repo or dataset path is linked from the results or methodology page |
| 2 | TRACE 87.0% useful content, 2,527 pages, median 1.75s / p95 8.91s, $0.79/1k, open source | VERIFIED, cadence wrong | README table + public dataset re-scored: 87.01%, 1.753s, 8.909s. results.md: $0.79 per 1,000 accessed pages. Cadence is weekly (Friday 00:15 UTC), not daily |
| 2b | TRACE runs | VERIFIED (offline) | `npm ci` exit 0 (818 packages), `npm run test:unit` exit 0 (8 checks), `npm run lint` exit 0. Collection needs Postgres + provider/judge keys; their methodology states a live rerun will not reproduce the same bytes |
| 3 | String 98.6% on 100 sites x 5 attempts (8,500 requests), open, per-protection winners | VERIFIED, two corrections | `npm run analyze` exit 0 reproduces the README table (string 493/500 = 98.6%). 8,500 = 100 x 5 x 17 providers; String alone = 500 requests. Akamai String = 82/85 = 96.5%, not 97.0% |
| 3b | String publishes an escalation ladder | REFUTED | README documents a two-class taxonomy (caller-selected tier vs server-side escalation) and one request per attempt with no client retries. No ladder, no rungs anywhere in README, CONTRIBUTING or the blog |
| 4 | WebCrawlerAPI 806-URL test: Context 92.1% e2e; F1 WebCrawlerAPI 0.623 > Context mainContent 0.567 > crawl4ai 0.562 > Firecrawl 0.539 | VERIFIED | Stored `results/*/results.json` recomputed after dropping 404 rows: F1 0.6231, 0.5679, 0.5622, 0.5374. e2e within 0.5 points of the page (their 61 dead URLs vs 71 recovered from error strings) |
| 5 | teracrawl 84.2% #1 coverage on scrape-evals | VERIFIED AS VENDOR CLAIM, not reproducible today | BrowserCash/teracrawl README + scrape-evals.png: Terracrawl tallest bar ~84% over 14 engines, order and values match scrape-evals' own table. Original firecrawl/scrape-evals is deleted; surviving fork martynasoxylabs/scrape-evals has 9 commits, none containing "teracrawl" (`git log -S teracrawl --all` empty) |
| 6 | Six leaderboards exist and are mappable | VERIFIED | Nimble, TRACE, String, WebCrawlerAPI, scrape-evals, SurfBench. Mapping table below |

## Corrections to the plan's own starting position

- "Firecrawl dethroned 2026-11-07" is not supported by any primary source.
  TRACE v6 (2026-11-07): context 94.4%, Firecrawl 92.9% (#2), Keenable 92.3%,
  Exa 84.9%. The surviving scrape-evals fork still lists Firecrawl #1 at 80.9%.
- scrape-evals 100-URL slice run 2026-11-08 (`phase=summary` log lines):
  crawl_engine 70.0% coverage / F1 0.388, crawl4ai 47.0% / 0.337, rest 48.0% /
  0.325. The Firecrawl slice row (7.0%) is invalid: 81 of 100 requests failed
  with `RateLimitError` at 10 req/min (`firecrawl_api.log`), only 9 credits
  spent (`fc_balance.txt`). Do not quote it.
- teracrawl 84.2% = 842/1000, 158 failures. The arithmetic in A4 is right; the
  date is not. The chart has no date; teracrawl's last commit is 2025-12-03.
- SurfBench exists and matches the plan's description: GitHub API returns
  riccardogiorato/surf-bench, 4 stars, "Open benchmark with judge panel",
  search + scrape + quest, pushed 2026-10-27.
- The plan's "semantic index serving 40% of calls from cache" is real but it
  is vendor copy: Firecrawl's own v2.5 post says the semantic index "already
  serves 40% of all API calls". No methodology is published for that number,
  so treat it as marketing, not a benchmark row.

## What each primary source actually says

### Nimble (llms.nimbleway.com/benchmarks, benchmarks-methodology)

- Dataset: 5,856 unique URLs, 2,107 domains, zero repeats. 5k open-web core +
  ~19% Google block. 104 URLs excluded (11 dead for every engine, 93 server
  5xx) plus 71 redirect-only; 5,856 scored.
- Render routing: 4,359 URLs on the fast default path, 1,497 JS-heavy URLs
  routed to full render. Each engine fetched every URL identically.
- Ground truth: verbatim excerpt confirmed by at least two independent
  extractors. Completeness = anchor recall over successful fetches. F1 = token
  recall x best-window precision.
- Two success definitions: raw fetch success (any non-empty 2xx) vs useful
  success (completeness >= 0.3, which drops 404 pages and anti-bot screens).
- Composite: 0.40 completeness + 0.30 success + 0.20 F1 + 0.05 latency
  (exp(-p95/10)) + 0.05 throughput. Configurable and disclosed.
- Nimble p95 19.5s vs Tavily 3.0s / Exa 4.6s. They buy completeness with tail
  latency on purpose.
- Disclosed limitation: rows where Nimble returned a server-side 5xx are
  excluded, which raises Nimble's measured success rate.

### TRACE (github.com/context-dot-dev/trace-benchmark)

- Cadence: sample every Friday 00:15 UTC, up to 300 new URLs per category,
  target ~2,500 max 2,700. 2,527 pages in the 2026-11-06 sample.
- "Useful content" (protocol v5): a provider-blind TypeSafe Jev 1.13.0 judge
  scores two questions, `content` (substantive / thin / blocked / error /
  unclear) and `identity` (match / wrong_target / unknown). Success requires
  substantive AND match. Partial content qualifies; completeness is not
  inferred. Evidence: first/middle/last 4,000 chars (12,000 max).
- Current protocol is v6, which measures page access only and explicitly
  ignores relevance, identity, completeness and usefulness. v5 and v6 scores
  are not comparable.
- Scoring: per-category success rate, then the arithmetic mean of category
  rates (8 core categories equal weight + Other). Failures, unresolved and
  pending stay in the denominator. Account-limit rejections (401/402/429)
  are excluded and reported separately. 95% CI from per-category binomial
  variance; pairwise exact McNemar tests on shared pages.
- Public data: /api/download returns page-level NDJSON with provider,
  category, success, error, elapsedMs. Private raw responses are not public.
- Reproducibility: published dataset + scoring code re-derive the leaderboard
  exactly (see commands). A live rerun will not reproduce the same bytes
  (their own stated limit).
- Cost: $0.79 per 1,000 accessed pages (context.dev), from results.md on the
  v6 run; it is not the cost per 1,000 useful-content pages.

### String (github.com/usestring/web-data-frontier-benchmark)

- Official run 2026-10-30: 100 targets x 5 attempts x 17 providers = 8,500
  requests. Pass = 2xx AND body contains the expected marker text.
  Concurrency 2, 90s timeout, default settings, no client-side retries.
- `npm run analyze` on the stored JSON reproduces all 17 rows.
- Per-protection, recomputed from official_results + tests.const:
  DataDome context_dev 100.0% (95/95); Akamai string 96.5% (82/85);
  Cloudflare string and firecrawl 98.7% (74/75); PerimeterX scrapedo and
  firecrawl 100.0% (30/30). Denominators exclude 12 targets that appear in
  neither the JSON nor the current fixture list.
- Drift: 12 of the run's target names (macys, ashleyfurniture, carters,
  databricks, expedia, alibaba, target, verizon, Rightmove, Vinted,
  TradingView, CoinMarketCap) are absent from the current tests.const.ts and
  README table, and 12 current fixtures are absent from the JSON. The README
  claim that every provider is sent "this same list" does not hold for the
  published run.
- The blog post is the older July 2026 run (90 sites, 15 providers, 6,750
  requests, string 95.8%). The repo README is the October run. Do not mix.
- "Rendering is mostly not the bypass": among caller-selected providers, 32
  of 100 targets passed by an unrendered provider alone vs none by a rendering
  provider alone (45 vs 2 requiring all five attempts). Bot defense keys on IP
  reputation and TLS fingerprint first.

### WebCrawlerAPI (webcrawlerapi.com/benchmarks/scrape-quality, WebCrawlerAPI/scraping-benchmark)

- Dataset: first 1,000 rows of firecrawl/scrape-content-dataset-v1. 133 rows
  marked broken, 61 dead (any tool 404) excluded for every tool, 806 scored.
- Coverage = >= 10% of truth_text tokens in markdown. Extraction F1 uses
  precision proxy 1 - lie_text overlap, because the dataset has no full noise
  text. Their harness says F1 is an approximation of Firecrawl's metric.
- Recomputed from stored results after excluding 404 rows (71 recovered vs
  their 61): e2e context 92.6%, Firecrawl 90.2%, WebCrawlerAPI 88.4%,
  crawl4ai 88.4%, Jina 87.4%, Spider 84.8%, Crawlbase 70.6%. F1: 0.1754,
  0.5374, 0.6231, 0.5622, 0.1829, 0.5228, 0.4957. Page values land within
  rounding; the small e2e gap is the dead-URL set.
- Context.dev mainContentOnly moves F1 0.176 -> 0.567, recall 82.7% -> 78.9%,
  success 92.1% -> 89.7%, p95 16.0s -> 19.8s.

### scrape-evals + teracrawl

- Surviving fork martynasoxylabs/scrape-evals (9 commits, full history
  searched): README table has 13 engines, Firecrawl #1 80.9% / F1 0.68. Runs
  in the fork: firecrawl 80.9, exa 76.3, tavily 67.6, scraperapi 63.5,
  zyte 62.9, scrapingbee 60.6, apify 60.2, crawl4ai 58.0, selenium 55.0,
  scrapy 54.0, puppeteer 53.7, rest 48.0, playwright 39.5, crawl_engine 70.0.
- teracrawl's own README claims "#1 coverage (84.2%) across 14 scraping
  providers" and links the deleted firecrawl/scrape-evals PR #13. Its chart
  (scrape-evals.png) shows Terracrawl ~84%, Firecrawl ~81%, Exa ~76%, Tavily
  ~68%, ScraperAPI ~63%, matching the fork's table plus one bar. Image alt
  text says 82.1%, which is stale.
- Verdict: the 84.2% bar is real as a historical vendor-published number, but
  no runnable scrape-evals checkout today contains teracrawl, and the original
  leaderboard is gone. Treat 84.2% as the aspiration, 80.9% (Firecrawl, fork
  README) as the bar you can actually be measured against.

## Commands and exit codes (fresh, 2026-11-08)

```
git clone --depth 1 https://github.com/context-dot-dev/trace-benchmark        exit 0
git clone --depth 1 https://github.com/usestring/web-data-frontier-benchmark   exit 0
git clone         https://github.com/WebCrawlerAPI/scraping-benchmark          exit 0
cd trace-benchmark && npm ci                                                   exit 0
npm run test:unit                                                              exit 0 (8 checks)
npm run lint                                                                   exit 0
curl -fsSG https://trace.context.dev/api/download --data-urlencode \
  'run=scraping-bench-v5:2026-11-06' -o trace_v5.ndjson                        exit 0 (10,108 rows)
# re-scored: context 87.01 / exa 79.06 / firecrawl 82.32 / keenable 84.16
# page:      context 87.0  / exa 79.1  / firecrawl 82.3  / keenable 84.2
# context latency: p50 1.753s, p95 8.909s (page 1.75s / 8.91s)
cd web-data-frontier-benchmark && npm ci                                       exit 0
npm run analyze -- --in official_results/benchmark-2026-10-30T18-48-18-489Z.json
npm test                                                                       exit 0 (16/16)
git fetch --unshallow && git log --all --oneline -S teracrawl -- README.md     exit 0, no hits
curl https://raw.githubusercontent.com/BrowserCash/teracrawl/main/scrape-evals.png
curl https://api.github.com/repos/riccardogiorato/surf-bench                  exit 0 (4 stars)
curl https://api.github.com/repos/Nimbleway/Public-benchmarks/contents/       exit 0 (4 dirs, no extraction)
```

## Six leaderboards mapped to harnesses we have or can wire

| # | Leaderboard | Primary artifact | Cadence | Our harness | Action |
| --- | --- | --- | --- | --- | --- |
| 1 | scrape-evals (1,000 URLs, coverage + F1) | firecrawl/scrape-evals (deleted); fork martynasoxylabs/scrape-evals | static | eval/research/scrape-evals-artifacts/run_harness.py + engines/crawl_engine.py | A1 slice done at 70%; A2 adapter PR; full 1,000 run |
| 2 | SurfBench (search + scrape + question quality) | github.com/riccardogiorato/surf-bench | static | plan B1 (no code yet) | port the scrape and question legs; skip search and say so |
| 3 | TRACE (page access + useful content) | context-dot-dev/trace-benchmark | weekly, Friday | none yet | add a provider entry in src/lib/benchmark/providers.ts pointing at a local /crawl shim; their public NDJSON can score us offline first |
| 4 | String Web Data Frontier (100 sites x 5 attempts) | usestring/web-data-frontier-benchmark | monthly | none yet | add src/providers/crawl.ts against the same shim; or feed their analyze our attempt JSON |
| 5 | WebCrawlerAPI scrape-quality (806 URLs, F1) | WebCrawlerAPI/scraping-benchmark | static | eval/research/bench_firecrawl.py shape | add a provider in benchmark.mjs, or score our stored markdown with their scoreRow offline |
| 6 | Nimble (DRACO, SimpleQA, Vertical, Extraction) | llms.nimbleway.com/benchmarks + Nimbleway/Public-benchmarks | static, 3-run verticals | our eval | extraction harness is not published, so steal the metric not the seat: completeness, useful success, composite weights |

## Steal list (what to adopt, with anchors)

| Steal | Source anchor | Adopt as | Our anchor |
| --- | --- | --- | --- |
| One request per attempt, no client retries, record the trace id | String README "How it works"; CONTRIBUTING "One request per attempt"; src/runner.ts executeRequest | A3 rung discipline: each rung sends once, scores what comes back, captures failure evidence | scripts/crawl_page.py http_attempts budget; tests/test_b45_unified_budgets.py |
| Completeness as anchor recall against verbatim ground truth; useful success at >= 0.3; raw vs useful reported separately | Nimble methodology, "Web extraction benchmark" | two new eval rows: completeness and useful-success, plus a raw-success row | eval/report.py coverage_pct; eval/runner.py score_result |
| Disclose render routing and pay the latency for it (4,359 fast / 1,497 full render, p95 19.5s) | Nimble methodology, dataset section | log the route class per URL in eval output; accept tail latency where completeness needs it | scripts/router.py _needs_render; ensemble gap-fill renderer |
| Access vs useful as separate scores, provider-blind judge, category-weighted mean, CI, paired McNemar | TRACE docs/methodology.md; src/lib/benchmark/providers.ts content and identity rubrics | split our scorecard into access and useful rows; add CI and a paired test to eval/report.py | eval/report.py; eval/runner.py |
| Crawl yield = unique useful pages / 1,000 requests | computed from the String/Nimble/TRACE denominators; no single source page | new eval headline next to coverage | scripts/crawl_frontier.py dedup gates (offer, mark_fetched + xxh3); scripts/dedupe.py bands_of; scripts/crawl_page.py crawl budget; learn/benchmarks/records.jsonl per-request success |
| Monthly re-run, published failures ledger, saved failure bodies | String README official_results + --failures-dir | keep D2's nightly cron; write per-failure evidence into the scrape-evals artifacts dir | D2 cron; eval/research/scrape-evals-artifacts/ |
| Rendering is not the bypass; IP reputation and TLS fingerprint come first | String README notes (32 vs 0 targets) | keep the ladder's cheap impersonation rungs first; only spend a render when the page shell proves JS | scripts/router.py js/bot gating; ensemble thin-page render rule |

## Known limits of this verification

- The Firecrawl extraction harness and the original scrape-evals leaderboard are
  not public, so those two cannot be re-run end to end from primary sources.
- TRACE collection was not run live (needs Postgres, four provider keys and a
  judge key). The published dataset and scoring path were re-run, which is the
  reproducible half.
- String's per-protection numbers use only the 88 targets shared between the
  published run and the current fixture list; the other 12 targets carry no
  anti-bot label in the current repo.
