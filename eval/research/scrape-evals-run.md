# scrape-evals: /crawl on Firecrawl's own methodology

**Headline:** On a deterministic 100-URL slice of Firecrawl's corpus, /crawl covers **70%** at avg F1 **0.388**; Firecrawl's API covers **75%** at **0.516**. The 5-point coverage gap is entirely anti-bot infrastructure (6 access losses vs 1 win, 0 extraction losses). Firecrawl still extracts better on pages both cover (F1 0.662 vs 0.563) because /crawl routes JS apps to static lanes and returns 200-status shells.

**Do they really beat everyone?** Firecrawl's 80.9% (full 1,000-URL corpus, their README) is the strongest number we can tie to the harness, and we reproduce the ordering on our slice (75% vs our 70%). Teracrawl self-reports **84.2% #1**, but its own screenshot says **82.1%**, and the citation (scrape-evals PR 13) points at a deleted repo, so it is unverified.

---

## 1. Harness acquisition

| Attempt | Result |
|--- | --- |
| `git clone github.com/firecrawl/scrape-evals` | **404** |
| `gh repo view` / `gh pr view 13` (authenticated) | **404** |
| web.archive.org PR 13 + repo, CDX query | no captures (`[]`) |
| `gh search repos scrape-evals` | public fork: **martynasoxylabs/scrape-evals** |

Working copy: a local clone of `martynasoxylabs/scrape-evals`, a fork of the deleted original.

Dataset authenticity: `datasets/1-0-0.csv` parsed rows, columns, and ids are identical to Firecrawl's official HuggingFace copy (`firecrawl/scrape-content-dataset-v1`). The scorer was independently corroborated against `TulipFarm/huntsman`'s MIT-attributed reimplementation (`dataset/eval.py`).

## 2. What coverage and quality mean, exactly

Scoring is deterministic Python (`evals/analysis/quality_analyzer.py`). No LLM, no judge, no human in the scoring loop. Humans only annotated the ground-truth snippets in the CSV.

**Coverage (`success_rate`)** — a task is covered iff ALL of:

1. `status_code` is 2xx/3xx (`200 <= status < 400`)
2. `error` is falsy
3. `content` is non-empty and `content_size > 0`
4. content contains none of 9 block markers: `attention required`, `cloudflare`, `verify you are a human`, `access denied`, `bot detection`, `datadome`, `akamai bot manager`, `imperva`, `sucuri website firewall`
5. **unless both `truth_text` and `lie_text` are empty → forced `False`** (frozen failures)

Frozen rows: **12/100** in our slice, **135/1,000** (13.5%) in the full corpus; 128 of those 135 carry a recorded `error` from Oct 2025 (Access Denied / 403 / page not found). They are unwinnable for every engine and cap maximum coverage at **86.5%**.

**Quality (`avg_f1`)** — averaged over ALL tasks, failures score 0:

1. tokenize: `re.findall(r"\d+/\d+|[\w'-]+", text.lower())`
2. slide a window of length = number of truth tokens over content tokens
3. each window scored by set overlap: recall = |window ∩ truth| / |truth|, precision = |window ∩ truth| / |window|
4. keep the window with best recall (ties: best precision)
5. F1 = harmonic mean of that window's precision/recall; mean across tasks

Markdown content is stripped (code fences, link/image syntax, emphasis, table pipes) before tokenizing; HTML-format content is tokenized raw, so tags count as tokens. The `lie_weight=4.0` parameter exists in the signature but is **never used** in the implementation; lie tokens only matter for the empty-both forced-fail check. "Quality %" = avg F1 × 100.

README's own wording: *"we refer to the F1 score as 'quality' for simplicity"*; coverage means *"successful content retrieval when the response returns a valid HTTP status (2xx/3xx), contains substantive content (content_size>0), and represents actual page content rather than access intermediary pages."*

## 3. /crawl adapter

`engines/crawl_engine.py` (in the fork) calls `/crawl`'s `scripts.ensemble.crawl` and maps only scored fields:

| ensemble result | ScrapeResult | notes |
| --- | --- | --- |
| `text` | `content` | `format="markdown"` (honest: ensemble emits markdown) |
| `status_code` | `status_code` | text with no status → inferred 200 |
| `error` | `error` | no text → 500 |
| `pointer` | dereferenced | read `pointer.out_dir/manifest` + sibling `text_full.txt` (the engine's own files, not a re-crawl) |
| `title`, `links` | dropped | not in `ScrapeResult`, not scored |

Operational guards:

- **75s adapter timeout** (`CRAWL_ADAPTER_TIMEOUT_S`, default 75) around a 4-worker executor; on timeout → `408` + `adapter_timeout: ensemble did not return within 75s (contract deadline 45s)`.
  - **Live-verified:** `CRAWL_ADAPTER_TIMEOUT_S=0.001` on `https://example.com` returned `{status_code: 408, error: "adapter_timeout: ensemble did not return within 0.001s (contract deadline 45s)"}`.
  - Why it exists: `cnn.com/climate` took **7m29s wall** in the first run (scrape_start 13:35:39 → scrape_done 13:43:08; engine-reported `elapsed_s=281`), which ran before the guard existed; tasks 521 and 611 took engine-reported 67s. `/crawl`'s own 45s contract deadline is not enforced inside `ensemble.crawl`.
  - Correction to an earlier snapshot: that first run **completed all 100 tasks** (summary written 13:43:09). The kill issued on a stale "99/100" reading was unnecessary; `--resume` re-analyzed the existing outputs without re-scraping and reproduced identical numbers (`success_rate=0.7`, `avg_f1=0.3882294340102095`).
- **Caches off**: `CRAWL_ENSEMBLE_CACHE=0`, `CRAWL_CAP_CACHE=0`.
- **Telemetry routed**: `set_source("benchmarks")` in `run_harness.py`.

Hygiene evidence: 110 telemetry records match my 100 slice URLs, **all `source="benchmarks"`**, timestamps 13:33–13:43 UTC (the /crawl run window). `learn/production/records.jsonl` contained no eval data; the single record now in it (wikipedia, 14:10 UTC) is from an unrelated process and that URL is not in the slice.

## 4. Runs

Slice: `rows[0::10]` of `datasets/1-0-0.csv`, written to `slice-100.csv` (deterministic, 100 rows). All runs: `--suite quality`, `--rerun`, caches off.

### 100-URL slice (this run)

| Engine | Coverage | Quality (F1) | F1 as % |
| --- | ---: | ---: | ---: |
| **/crawl** (`crawl_engine`) | **70.0%** | **0.388** | 38.8% |
| **Firecrawl API** | **75.0%** | **0.516** | 51.6% |
| Rest (requests) | 48.0% | 0.325 | 32.5% |
| Crawl4AI | 47.0% | 0.337 | 33.7% |
| Playwright | 28.0% | 0.199 | 19.9% |

All five engines completed 100/100 tasks; each row's summary line and `runs/results/<engine>_quality.json` agree (verified fresh).

### Fork-committed full-corpus reference (1,000 URLs, their README)

| Engine | Coverage | Quality |
| --- | ---: | ---: |
| Firecrawl | 80.9 | 0.68 |
| Exa | 76.3 | 0.53 |
| Tavily | 67.6 | 0.50 |
| ScraperAPI | 63.5 | 0.45 |
| Zyte | 62.9 | 0.47 |
| ScrapingBee | 60.6 | 0.45 |
| Apify | 60.2 | 0.42 |
| Crawl4AI | 58.0 | 0.45 |
| Selenium | 55.0 | 0.40 |
| Scrapy | 54.0 | 0.43 |
| Puppeteer | 53.7 | 0.41 |
| Rest (requests) | 50.6 | 0.36 |
| Playwright | 39.5 | 0.34 |

`scrapling` and `trafilatura` are **not wired as engines** in this harness (no engine files; they are /crawl lanes). The engines we could run locally were /crawl, rest, Crawl4AI, Playwright, plus Firecrawl via API.

### Patches applied (all in the fork, disclosed; /crawl repo untouched)

| File | Why |
| --- | --- |
| `engines/base.py` | registered `crawl_engine` |
| `engines/crawl4ai_scraper.py` | their `suppress_output` closes a devnull that background threads still write to → `ValueError: I/O operation on closed file`, 1/5 covered in smoke. Persistent `self._devnull` instead. |
| `engines/firecrawl_api.py` | (a) docstring claims `maxAge=0` but code never passed it; (b) no throttle. First run at `--max-workers 4` got **91/100 `RateLimitError`** (7% coverage, 9 credits). Added 1.05s global spacing, 45s rate-limit retry, and `maxAge=0`. Re-run: 0 rate limits. |
| `engines/playwright_scraper.py` | `BrowserType.launch(devtools=...)` was removed in Playwright 1.64 → all 100 failed instantly. Dropped the kwarg (always `False` headless). |

### Firecrawl budget

| | Credits |
| --- | ---: |
| Start of session | 1,376 |
| After smoke (5 URLs) | 1,350 |
| After rate-limited run (9 billable) | 1,322 |
| After throttled slice run | **1,092** |

Slice run cost **230 credits** (markdown + render, `maxAge=0`); session total spent ≈ 284, under the 300 budget. Key never printed.

## 5. /crawl vs Firecrawl, per-URL

### Head-to-head on the 100-URL slice

| | Count |
| --- | ---: |
| Both cover | 69 |
| /crawl only | 1 |
| Firecrawl only | 6 |
| Neither | 24 |

### /crawl's 6 losses to Firecrawl

| URL | /crawl reason | Category |
| --- | --- | --- |
| totaltools.com.au/... | 403 bot challenge | infra moat |
| worthingtonagparts.com/catalog-request | 403 bot challenge | infra moat |
| hardeeso.com/join_our_team/... | 403 bot challenge | infra moat |
| comc.com/Cards/Baseball/... | 403 bot challenge | infra moat |
| Brightarchitecturalsolutions.co.uk | 401 auth required | infra moat |
| researchgate.net/publication/... | 429 rate limited | infra moat |

**0 of 6 are extraction failures.** /crawl's one win: `xt.com/etfIndex/defiindex_usdt` (Firecrawl returned 404).

### /crawl's full 30-failure breakdown (coverage view)

| Reason | Count |
| --- | ---: |
| 403 / bot challenge | 12 |
| 404 dead URL (corpus is ~1 year old) | 10 |
| Fetch error / no HTTP status | 3 |
| Frozen auto-fail (empty truth+lie) | 3 |
| 401 auth required | 1 |
| 429 rate limited | 1 |

The 3 fetch errors (tasks 391, 521, 611) are annotated rows: no lane returned text, elapsed 42–68s (521/611 exceeded the 45s contract deadline).

### The quality gap: extraction, on covered pages

Coverage hides the extraction gap; F1 exposes it. On the 69 pages both engines cover:

| | avg F1 |
| --- | ---: |
| /crawl | 0.563 |
| Firecrawl | 0.662 |
| paired mean diff | −0.099 |

Firecrawl scores higher on 45 pages, /crawl on 17, tie 7.

Concrete examples (all /crawl 200 + content → counted as **covered**, but near-zero F1):

| Task | /crawl output | /crawl F1 | Firecrawl F1 |
| --- | --- | ---: | ---: |
| 111 `v17.angular.io/docs/...` | 88 chars: *"This website requires JavaScript."* (raw 4.4KB) | 0.097 | 0.990 |
| 271 `bni.com.tw/zh-TW/memberdetails...` | 275 chars of nav/header (raw 29.9KB) | 0.000 | 0.923 |
| 511 `social27.com/ai-solutions` | 243 chars (raw 93.8KB) | 0.119 | 1.000 |
| 221 `gov.br/mdic/.../presidente-sanciona-lei...` | 7,233 chars, deep-body section | 0.196 | 0.870 |

**Root cause:** these URLs were classified `page_type=static`/`documentation`, whose lane sets contain no browser lane (`PAGE_TYPE_LANES` documentation = `["trafilatura", "scrapling", "httpx"]`); the router ran `curl_cffi` only. The JS app shell comes back 200 with non-empty text, so it passes every coverage check and the block-marker list, while the truth snippet is absent → F1 near zero. Firecrawl renders the JS and gets the full page.

**Fix (next):** detect JS shells (text/raw ratio, `"requires javascript"` marker, or a cheap headless probe) and fall back to a browser lane; treat shell-sized text on large raw HTML as a partial, not a win. This is the one gap that is ours, not their infra.

### Winnable-row view (excludes the 12 frozen rows)

| Engine | Coverage on 88 winnable rows | Avg F1 |
| --- | ---: | ---: |
| /crawl | 70/88 = 79.5% | 0.441 |
| Firecrawl | 75/88 = 85.2% | 0.586 |

Same 5.7-point coverage gap; still all anti-bot. (Frozen rows are reported under their methodology's raw denominator above.)

## 6. Teracrawl cross-check

From `BrowserCash/teracrawl` README (raw, 9,943 bytes):

> **Line 51:** "Teracrawl achieves **#1 coverage (84.2%)** across 14 scraping providers on the [scrape-evals](https://github.com/firecrawl/scrape-evals/pull/13) benchmark, an open evaluation framework that tests web scrapers against 1,000 diverse URLs for success rate and content quality."
>
> **Line 50 (image alt):** `Teracrawl achieves #1 coverage at 82.1%`

| Check | Result |
| --- | --- |
| Citation (PR 13) | repo deleted; PR unreachable; archive has no captures |
| Internal consistency | **84.2% in prose vs 82.1% in screenshot alt** |
| Denominator | full 1,000-URL corpus; 135 rows frozen → max 86.5% |
| What 84.2% implies | 842/1,000, i.e. 97.3% of the 865 winnable rows |

We could not reproduce or independently verify teracrawl's number: no teracrawl engine, no surviving PR, and our slice is a different denominator. Treat 84.2% as self-reported; the 82.1% alt text suggests the prose figure may be stale.

## 7. Playwright note

Their Playwright engine launches a fresh browser per URL and waits for `networkidle` (30s timeout). It completed 100/100 here but was the weakest engine: **28.0% coverage / 0.199 F1**, below its own fork-committed full-corpus figure (39.5% / 0.34). Failure reasons from our run: 33× `Page.goto` 30s timeout, 14× 403, 7× 404, 2× download-started, 2× 401, 1× DNS, 1× 454. Coverage also drops clean 2xx fetches when content is a block page or the HTML carries no substantive size. Raw-HTML format is tokenized with tags, which drags precision.

## 8. Verdict and next actions

1. **Coverage:** /crawl 70% vs Firecrawl 75% on the same 100 URLs. The entire gap is 403/challenge, 401, and 429 (6 losses, 1 win). That is their proxy/m residential-IP moat, not extraction.
2. **Quality:** /crawl 0.388 vs Firecrawl 0.516. On covered pages the gap is real (0.563 vs 0.662) and traces to JS app shells returned by static lanes.
3. **Do they really beat everyone?** On the harness's own full-corpus table Firecrawl leads the open field (80.9% / 0.68), and we reproduce that ordering on our slice. Teracrawl's claimed 84.2% #1 is self-reported, contradicted by its own screenshot (82.1%), and cites a deleted PR — unverified.
4. **Next:** add JS-shell detection + browser fallback in /crawl, then re-run this exact slice; expected F1 gain on tasks 111/271/511/221 and similar, with coverage unchanged.

### Artifacts

Shipped in this repo:

- Slice: `eval/research/scrape-evals-artifacts/slice-100.csv`
- Comparison data: `eval/research/scrape-evals-artifacts/compare.json`
- Adapter: `engines/crawl_engine.py` in the scrape-evals PR, not this repo

Not shipped: the launcher, per-engine logs, and the diag sidecar. Those are
run output from the private workspace. The headline numbers above recompute
from `compare.json` alone; the README carries the command.
