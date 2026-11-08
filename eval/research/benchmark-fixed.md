# Benchmark fixed: presence means the data is there

Verdict: all seven contract changes landed, TDD, one commit each. `make eval`
exits 0, 22/22 completeness, 33/33 gated sentinels (2 pending), applicable-cell
recall 98.9% (94/95, blocked = N/A), blocked rate 9.1%, regression set 34.3%
against a recomputed floor of 34.3. The full-set fixture-ceiling coverage moved
44.1% -> 43.7% because the scorer now tells the truth about truncation and
lone-key metadata; it is not a regression, it is the old number being corrected.

## Gate evidence (fresh, 2026-11-07 17:27-17:28 UTC, code-complete tree)

The tree tested is the final code; the only commits after it are this report
and `docs/CONTRACT.md`.

Command 1: `.venv/bin/python -m pytest tests/test_eval_runner.py -q`

```
27 passed in 89.22s (0:01:29)
```

exit code 0.

Command 2: `make eval`

```
Eval complete: 22 fixtures, 22/22 completeness match, avg field coverage 43.7%, avg elapsed 1.08s
Coverage regression-set (13 fixtures): 34.3%
Coverage full-set (22 fixtures): 43.7% | Applicable-cell recall: 98.9% (94/95, blocked fixtures = N/A)
Blocked rate: 9.1% (2/22 fixtures)
Sentinels: 33/33 gated passed, 2 pending
Results: eval/results/eval_20261107_172817.jsonl
OK eval
```

exit code 0. An earlier identical run at 17:24 (`eval_20261107_172404.jsonl`)
returned the same four numbers. `large_page` and `pdf_sample` are still in the
fixture list and still scored; neither was dropped.

## Headline: old vs new

Baseline is `eval/results/eval_20261107_161625.jsonl`, the challenger's own
fresh run of the old scorer (126 cells, 44.1% full, 35.5% regression, floor
32.6). New is `eval/results/eval_20261107_172817.jsonl`.

| quantity | old scorer | new scorer |
| --- | --- | --- |
| full-set fixture-ceiling coverage | 126/286 = 44.1% | 125/286 = 43.7% |
| regression set (13 fixtures) | 60/169 = 35.5%, floor 32.6 | 58/169 = 34.3%, floor 34.3 |
| non-regression set (9 fixtures) | 66/117 = 56.4% | 67/117 = 57.3% |
| applicable-cell recall | 97/100 = 97.0% (miss-matrix detector, blocked included) | 94/95 = 98.9% (`source_applicable_fields`, blocked = N/A) |
| blocked rate | not reported | 9.1% (2/22) |
| completeness | 22/22 | 22/22 |
| gated sentinels | 32/32, 2 pending | 33/33, 2 pending (+1 citations ground-truth sentinel) |

Field flips between the two runs, old boolean -> new boolean:

| fixture | field | flip | caused by |
| --- | --- | --- | --- |
| `large_page` | `raw_html` | True -> False | change 1, 8192/714007 = 1.1% share |
| `metadata_rich_page` | `images` | False -> True | change 3, `image_variants` carries the og:image |
| `metadata_rich_page` | `citations` | False -> True | change 6, real References block |
| `pdf_sample` | `metadata` | True -> False | change 7, one valued key is not depth |
| `spa_delay_page` | `metadata` | True -> False | change 7, same |

Net 126 -> 125 cells. Regression loses 2 cells (large_page, pdf_sample), the
non-regression set gains 1 (metadata_rich_page +2, spa_delay_page -1).

## Per change

### 1. `raw_html` is a completeness ratio, not an 8192-byte window

- Old rule: any non-empty string. The producer returns the first 8192 bytes of
  the response, so `large_page` scored present at 8192/714007 = 1.1% coverage.
- New rule: `RAW_HTML_MIN_RATIO = 0.20`. The returned body must cover at least
  20% of the source page's bytes, measured by `_fixture_bytes(fixture_name)`.
- Documented exception: when the source size is unknown (unit probes, a fixture
  missing from disk), the ratio cannot be computed and the field scores 0.
  There is no byte-window fallback.
- Isolated impact: `large_page` 4/13 -> 3/13. Full set 126 -> 125 cells
  (44.1% -> 43.7%); regression 60 -> 59 (35.5% -> 34.9%).
- Test: `TestBenchmarkContractFixes.test_raw_html_must_cover_a_real_share_of_the_page`
  (out-of-process probe; red first with `TypeError: _field_present() takes 2
  positional arguments but 3 were given`, then green).
- Contract: `docs/CONTRACT.md` section 12.

### 2. `tables` scores rows in `tables_raw`, never the bare count

- Old rule: `bool(value)` for a scalar. `{"tables": 3}` scored present.
- New rule: `_tables_have_rows` requires `tables_raw` to be a list holding a
  markdown table with a header, a delimiter and at least one data row (3
  non-blank lines). `score_result` reads `tables_raw` for the `tables` field;
  the `tables` integer is telemetry.
- Isolated impact: 0 cells on the current 22 fixtures. `article_with_tables`
  and `tables_span_page` were already True and stay True through their rows;
  `pdf_sample` was False and stays False. The win is the closed hole: a
  `tables: 3` count no longer passes.
- Test: `TestBenchmarkContractFixes.test_tables_scores_rows_in_tables_raw_not_the_count`
  (red first: `AssertionError: False is not true : tables: 3 is a count, not rows`).
- Contract: `docs/CONTRACT.md` section 12.

### 3. `images` is credited when `image_variants` carries real variants

- Old rule: non-empty `images` list. `metadata_rich_page` ships its og:image
  only in `image_variants` (`source: meta`), so `images` stayed empty.
- New rule: when `images` does not score, `score_result` credits it from
  `image_variants` through the same `_list_of_url_records(..., ("url","src"))`
  shape check, so relative-only variants still score 0.
- Isolated impact: `metadata_rich_page` 8/13 -> 9/13. Full set 126 -> 127
  cells (44.1% -> 44.4%); non-regression 66 -> 67 (56.4% -> 57.3%); regression
  unchanged.
- Test: `TestBenchmarkContractFixes.test_images_are_credited_from_image_variants`
  (red first: `AssertionError: False is not true : a real og:image variant is
  an image the page carries`).
- Contract: `docs/CONTRACT.md` section 12.

### 4. `_field_present` has no `bool()` fallback for counts

- Old rule: `bool(value)` for every field except the five shape-checked side
  channels. An `int`, `float` or `True` scored; a whitespace-only string scored.
- New rule: bool/int/float return False (a count or flag is telemetry); lists
  and dicts score on length; strings score on `.strip()`.
- Isolated impact: 0 cells on the current 22 fixtures. `pdf_sample.tables = 0`
  was already False. The unit test is the deliverable: `{"tables": 3}` and
  `{"title": 1}` now score 0.
- Test: `TestBenchmarkContractFixes.test_counts_and_flags_do_not_score_as_presence`
  (red first: `AssertionError: True is not false`).
- Contract: `docs/CONTRACT.md` section 12.

### 5. Headline prints applicable-cell recall and blocked rate

- Old output: avg field coverage, regression-set coverage, full-set coverage.
  No applicable recall, no blocked rate; a blocked fixture's 13 cells counted
  as ordinary misses.
- New output: `applicable_summary(results)` prints beside full-set coverage,
  and a `Blocked rate` line. Blocked fixtures are N/A: their cells leave the
  applicable denominator. Fixture-ceiling coverage still includes them (they
  score their real field values), so blocked pages are never counted as
  extraction successes.
- Applicable detection: `source_applicable_fields(fixture_name)` scans the
  fixture source for the markup that produces each field, with a 30-word floor
  for text; PDFs are read with pypdf. It under-detects by design, so recall is
  a lower bound, and it is always printed next to the fixture-ceiling number.
- Detector iteration, disclosed: the first full run with this scorer printed
  97.9% (94/96) because the scan treated `<meta charset>` on `spa_delay_page`
  as a metadata signal; that page declares no metadata. The rule now requires
  a `name`/`property`/`itemprop` attribute on the `<meta>` tag. New number:
  98.9% (94/95). The one remaining applicable miss is `large_page.raw_html`,
  the truncation change 1 exists to expose.
- Tests: `TestBenchmarkContractFixes.test_applicable_recall_excludes_blocked_and_reports_blocked_rate`
  (red first: probe failed, `source_applicable_fields` did not exist) and
  `TestBenchmarkContractFixes.test_headline_prints_applicable_recall_and_blocked_rate`
  (red first: `'Applicable-cell recall' not found in stdout`).
- Contract: `docs/CONTRACT.md` section 12.

### 6. Citations: one fixture with real citation markup (chosen), not N/A

Decision: give `metadata_rich_page` a real References block. The task offered
two options, and the challenger's reasoning picks this one:

- The challenger calls citations "the one defensible removal" only if the
  zero-ground-truth reason is stated and both numbers are published. Removal
  from `SCORED_FIELDS` would break `eval/research/bench_allinone.py:561`, which
  asserts the tuple against `EXPECTED_FIELDS`; that file is not mine to edit.
  It would also shrink the denominator, the same shape the challenger flags as
  gaming for `json_ld` and `tables`.
- The miss-matrix F3 action is "either add one fixture with real
  `<ol class="citations">` / footnote markup and assert the cited URLs, or drop
  citations until such a fixture exists." Adding the fixture keeps the 13-field
  contract and creates correctness validation instead of shape-only validation.
- I did not mark citations N/A-ground-truth, because now there is ground truth.
  N/A is the honest label only while zero fixtures carry markup.

Implementation: `metadata_rich_page.html` gained a `class="references citations"`
list and a `class="footnote"` paragraph with absolute cited URLs, written inline
in sentences so trafilatura keeps the links. The crawl now emits three citation
records; a gated sentinel asserts
`citations contains https://example.org/ref/capability`.

- Isolated impact: `metadata_rich_page` 8/13 -> 9/13. Full set 126 -> 127 cells
  (44.1% -> 44.4%); non-regression 66 -> 67 (56.4% -> 57.3%); regression
  unchanged.
- Tests: `TestBenchmarkContractFixes.test_citations_have_a_fixture_with_real_citation_markup`
  (red first: `AssertionError: False is not true : citations must score; got
  None`) plus the new gated sentinel.
- Contract: `docs/CONTRACT.md` section 12.

### 7. `metadata` needs 2+ distinct valued keys

- Old rule: `len(dict) > 0`. One always-emitted key scored, which made
  metadata an always-on 21/22 cell.
- New rule: `_metadata_has_depth` counts keys carrying a value (blank strings
  and None do not count) and requires 2. A dict with 2 keys where one is `""`
  scores 0.
- Isolated impact: `pdf_sample` 3/13 -> 2/13 and `spa_delay_page` 4/13 ->
  3/13. Full set 126 -> 124 cells (44.1% -> 43.4%); regression 60 -> 59
  (35.5% -> 34.9%); non-regression 66 -> 65 (56.4% -> 55.3%).
- Test: `TestBenchmarkContractFixes.test_metadata_needs_two_distinct_keys_with_values`
  (red first: `AssertionError: True is not false : one key is not metadata depth`).
- Contract: `docs/CONTRACT.md` section 12.

### Floor recomputed from the new scorer

`REGRESSION_COVERAGE_FLOOR` 32.6 -> 34.3, measured as 34.3% on four
consecutive full runs with identical per-fixture field counts
(171618, 171654, 171724, 171804, plus the gate run 172404). The test that
pins the baseline was updated first (red: `AssertionError: 32.6 != 34.3`), then
the constant.

Test: `TestRegressionGuard.test_floor_is_the_measured_regression_baseline`.

## The six rejected gaming moves: none implemented

| rejected move | status |
| --- | --- |
| Publishing applicable recall alone | Not done. Both numbers print on the same summary lines. |
| Counting blocked pages as coverage successes | Not done. Blocked pages still score their real fields in fixture-ceiling coverage and are excluded only from the applicable denominator; blocked rate is its own line. |
| Dropping `json_ld` or `tables` from `SCORED_FIELDS` | Not done. All 13 fields remain; `SCORED_FIELDS` is byte-identical, so the `bench_allinone.py:561` tuple assert still holds. |
| Scoring markdown on presence only | Not applicable; `markdown` is not in `SCORED_FIELDS` and no scoring rule changed for it. |
| Iframe-as-link without the recaptcha/turnstile exclusion | Not touched. `links` scoring is unchanged. |
| Padding fixtures with fields we already win, or dropping `large_page` / `pdf_sample` | Not done. Both fixtures remain. The one fixture edited (`metadata_rich_page`) gained `citations`, a field it did not win before. |

## Files changed

| file | change |
| --- | --- |
| `eval/runner.py` | all seven scorer changes, `source_applicable_fields`, `applicable_summary`, headline print, floor 34.3, citations sentinel |
| `eval/fixtures/metadata_rich_page.html` | References block with absolute cited URLs (change 6) |
| `tests/test_eval_runner.py` | 8 new contract tests, stub probe updated to the new contract shape, floor assertion 32.6 -> 34.3 |
| `docs/CONTRACT.md` | new section 12: eval scoring contract (presence rules, headline, gate) |
| `eval/research/benchmark-fixed.md` | this report |

Commits, one per change (plus docs): `e6fb3e6` (1), `9a9e42b` (2), `e348d06`
(3), `9dffd09` (4), `797a02b` (7), `a8ad273` (6), `0952417` (5, amended with
the charset detector fix), `d088de0` (floor), then this report and
`docs/CONTRACT.md`.

## Notes and risks

- Commit `e6fb3e6` also carried four probe-file renames
  (`eval/research/probe_*.py` -> `archive/`) that were already staged in the
  shared index by a concurrent lane. My later commits use
  `git commit -- <paths>` so no other lane's work is swept in again.
- `source_applicable_fields` is a heuristic that under-detects; recall is a
  lower bound and must never be published without the fixture-ceiling number.
- Concurrent lanes have unstaged edits in `scripts/crawl_page.py` and
  `scripts/ensemble.py`. Both gate runs above (17:24 and 17:28) were made with
  those edits present in the worktree and returned identical numbers; if they
  land as new commits, re-run `make eval` before trusting the numbers.
- Skipped: no run of `bench_allinone.py` (online competitor benches, and its
  only dependency on my lane, the `SCORED_FIELDS` tuple, is unchanged).
