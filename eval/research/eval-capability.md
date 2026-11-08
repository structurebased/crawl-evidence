# Eval capability: the headline now measures /crawl, not the fixtures

**Verdict.** Raw coverage counted 140 cells as misses that no correct tool
could score, because the source never carried the structure. The eval now
leads with applicable-cell recall: **96.8%** of cells whose source carries
the field were extracted. Coverage stays on the page as the fixture-limited
secondary: **52.0%**.

Fresh `make eval`: exit 0, 25/25 completeness match, 75/75 gated sentinels
passed, 2 pending (both pre-existing, neither on a full-surface fixture).

## The math

Coverage's denominator is `fixtures x 14`. When most fixtures are pages
that never had json_ld, tables, or images, those cells are empty by
construction. Returning nothing for a field the source never had is
correct, but the denominator still calls it a miss.

Both rows below use the same scorer, from
`eval/results/eval_20261107_213245.jsonl`; the 22-fixture row simply
excludes the 3 new pages.

| | OLD (22 fixtures) | NEW (25 fixtures) |
| --- | --- | --- |
| raw cells | 308 | 350 |
| present | 140 | 182 |
| **raw coverage** | **45.5%** | **52.0%** |
| applicable cells | 114 | 156 |
| applicable hits | 109 | 151 |
| **applicable-cell recall** | **95.6%** | **96.8%** |
| inapplicable misses | 140 | 140 |
| blocked cells | 28 | 28 |
| applicable misses | 5 | 5 |

Raw coverage here is the exact present/cells ratio. The runner prints 45.4%
for the old row, because it averages per-fixture percentages that are each
already rounded to one decimal; the 0.1 is a rounding artifact, not a
scoring difference.

The 3 new fixtures added 42 cells: 42 applicable, 42 present, 42 hits.
Nothing that was a miss before became a hit by weakening a check.

### Where the 350 raw cells go

| Class | Cells | Share of raw denominator |
| --- | --- | --- |
| present | 182 | 52.0% |
| inapplicable miss (source lacks the structure) | 140 | 40.0% |
| blocked-fixture cells (challenge_page, empty_page) | 28 | 8.0% |
| applicable miss (real capability loss) | 5 | 1.4% |

40% of the raw denominator is barren. The 5 applicable misses are the
entire capability gap.

### Headline before and after

Before, the print led with coverage:

```
Coverage full-set (22 fixtures): 45.4% | Applicable-cell recall: 95.6% (109/114, blocked fixtures = N/A)
```

(45.4% is the value the runner prints for that row; the exact 140/308
ratio is 45.5%. See the rounding note above.)

After, `make eval` leads with capability:

```
CAPABILITY = Applicable-cell recall: 96.8% (151/156); source-applicable cells only, blocked fixtures = N/A
Coverage full-set (fixture-limited secondary, 25 fixtures): 52.0% (floor 52.0%)
Coverage regression-set (original 13 fixtures): 36.3% (floor 36.3%)
```

Coverage is still printed, still gated, still honest. It is just no longer
the headline, because it measures the fixtures as much as the tool.

## Applicability is derived from the source, not hand-maintained

`source_applicable_fields(fixture)` scans the fixture's own bytes at
scoring time. There is no per-fixture applicability list to keep in sync.
The predicates (`eval/runner.py`):

| Field | Source signal |
| --- | --- |
| text, markdown | 30+ body words, scripts stripped |
| markdown (short pages) | else an `h1`-`h6` or `table` tag |
| title | a `<title>` tag |
| links, links_meta | an `<a href=...>` |
| images, image_variants | `og:image`, `<img`, or `srcset` |
| metadata | a `<meta name/property/itemprop=...>` |
| json_ld | `ld+json` |
| raw_html | always, every served page has bytes |
| tables | a `<table>` tag |
| date | `datetime`, `datePublished`, or `article:published_time` |
| iframes | an `<iframe>` tag |
| citations | a class containing `reference`, `footnote`, or `citation` |

PDFs use pypdf: 30+ extracted words for text, a non-empty `/Title` for
title. Blocked fixtures leave the recall denominator entirely and are
reported on the blocked-rate line instead.

The docstring calls this "an applicable floor, not ground truth". A field
with no detectable source signal is never counted as a miss.

## Per-fixture sentinel table

Each page carries one sentinel per scored field, 42 total. All 42 are green
on the live crawl: 0 pending, 0 failed.

| Fixture | Size | Fields | Applicable | Sentinels green | Pending | Failed |
| --- | --- | --- | --- | --- | --- | --- |
| full_surface_product | 49,854 B, 458 lines | 14/14 | 14 | 14/14 | 0 | 0 |
| full_surface_news | 46,392 B, 465 lines | 14/14 | 14 | 14/14 | 0 | 0 |
| full_surface_docs | 47,005 B, 648 lines | 14/14 | 14 | 14/14 | 0 | 0 |

Where each needle rides in the source:

| Field | Source structure | product | news | docs |
| --- | --- | --- | --- | --- |
| title | `<title>` text | -PRODUCT-TITLE | -NEWS-TITLE | -DOCS-TITLE |
| text | body paragraph opening | -PRODUCT-TEXT | -NEWS-TEXT | -DOCS-TEXT |
| links | anchor href query | -PRODUCT-LINK | -NEWS-LINK | -DOCS-LINK |
| links_meta | anchor `title` attribute | -PRODUCT-LINKS-META | -NEWS-LINKS-META | -DOCS-LINKS-META |
| images | largest srcset candidate URL | -PRODUCT-IMG | -NEWS-IMG | -DOCS-IMG |
| metadata | `<meta content>` value | -PRODUCT-META | -NEWS-META | -DOCS-META |
| json_ld | `keywords` array in the graph | -PRODUCT-JSONLD | -NEWS-JSONLD | -DOCS-JSONLD |
| raw_html | `data-ref` attribute on a form/note/heading | -PRODUCT-RAWHTML | -NEWS-RAWHTML | -DOCS-RAWHTML |
| tables | `<td>` cell in a data table | -PRODUCT-TABLE | -NEWS-TABLE | -DOCS-TABLE |
| date | page date value | 2026-09-02 | 2026-10-22 | 2026-10-30 |
| iframes | iframe `src` query | -PRODUCT-IFRAME | -NEWS-IFRAME | -DOCS-IFRAME |
| image_variants | `<img alt>` value | -PRODUCT-ALT | -NEWS-ALT | -DOCS-ALT |
| citations | cited prose-link href query | -PRODUCT-CITATION | -NEWS-CITATION | -DOCS-CITATION |
| markdown | `<code>` block body | -PRODUCT-MARKDOWN | -NEWS-MARKDOWN | -DOCS-MARKDOWN |

The `images` rows ride the srcset candidate that lands in `image_variants`;
the scorer's `images` check reads that channel when the flat `images` list
is empty (the og:image-only contract), and `_sentinel_haystack` now resolves
the same value so the sentinel and the presence check cannot disagree. The
`tables` rows ride a `<td>` because `tables_raw` carries cells, not
captions. The `raw_html` rows ride an attribute markdown never emits, so the
needle is found only in `raw_html`.

Sentinel gate totals across all fixtures: `75/75 gated passed, 2 pending`.
The 2 pending are pre-existing and unrelated:

- `shadow_dom_page`, `SENTINEL-JS-SHADOW-TEXT`, needs the B5-B7 browser lane
  to pierce runtime shadow roots.
- `social_rehydration_page`, `SENTINEL-REHYDRATION-AUTHOR`, needs a live
  tiktok.com host for the B26 rehydration lane.

## Floor movement

| Floor | Before | After | Why |
| --- | --- | --- | --- |
| Regression (original 13 fixtures) | 36.3% | 36.3% | Set unchanged (W3); the 3 new pages are not in it and score 100% |
| Full suite (25 fixtures) | none | **52.0%** | New: mean field coverage over every fixture, including the 3 new pages |
| Full-set coverage baseline | 45.5% | 52.0% | 42 new cells, all present |

The regression floor stays pinned to the original 13 by W3: the richer
batch must never weaken the gate those pages define. But the richer batch
and the full-surface pages previously had no floor at all, only the
completeness and sentinel lines. `FULL_COVERAGE_FLOOR` closes that hole: a
full run now fails if the suite average drops below 52.0%, even when the
original 13 are untouched. A `--fixture` subset run is exempt, because the
suite average is not a property of one page.

Tests: **38 passed** (was 37). The new test
`test_full_suite_floor_gates_only_full_runs` pins the branch that
`make eval` cannot prove fails: full suite below floor must fail, subset
below floor must not.

## Fresh-run evidence

Command: `make eval`
Result: exit 0 (the full log is retained in the private repo's eval build output)

```
Eval complete: 25 fixtures, 25/25 completeness match, avg field coverage 52.0%, avg elapsed 0.18s
CAPABILITY = Applicable-cell recall: 96.8% (151/156); source-applicable cells only, blocked fixtures = N/A
Coverage full-set (fixture-limited secondary, 25 fixtures): 52.0% (floor 52.0%)
Coverage regression-set (original 13 fixtures): 36.3% (floor 36.3%)
Markdown integrity: 20/25 present | headings 122/126 round-trip | tables 28/28 preserved | tag leaks 0
Blocked rate: 8.0% (2/25 fixtures)
Sentinels: 75/75 gated passed, 2 pending
Results: eval/results/eval_20261107_213245.jsonl
```

`.venv/bin/python -m pytest tests/test_eval_runner.py -q`:
`38 passed in 30.91s`, exit 0.

## The 5 applicable misses, named

These are the real capability loss, not fixture barrenness:

| Fixture | Missed fields |
| --- | --- |
| large_page | text, title, markdown |
| shadow_dom_page | markdown |
| spa_delay_page | markdown |

Four of the five are markdown. `eval/research/markdown-scoring.md` covers
the markdown-absent fixtures and the producer history. None of the 3
full-surface fixtures contributes an applicable miss.

## What this does not do

- It does not raise coverage by dropping fixtures, shrinking denominators,
  or hand-editing applicability. The only denominator change is excluding
  blocked fixtures from recall, which is reported separately as the
  blocked rate.
- It does not make applicable recall a perfect measurement. The docstring
  is explicit: the source scan under-detects, so recall is a floor. A
  field whose source signal the heuristic misses drops out of the
  denominator instead of counting as a miss.
- It does not gate the 3 new fixtures on coverage alone. They are held by
  completeness, 14 sentinels each, and the full-suite floor.
