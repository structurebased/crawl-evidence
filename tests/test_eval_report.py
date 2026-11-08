#!/usr/bin/env python3
"""tests/test_eval_report.py — W3 dual-number coverage in the eval report.

Contract: the scorecard prints both the 13-field coverage headline and the
old 8-field coverage for every tool row, with both denominators named in
the table header. A run is never compared across a denominator change.

The 5 new side channels (date, iframes, links_meta, image_variants,
citations) move the headline by up to 5/13 = 38.5 points while leaving
old-field recall untouched; that split must be visible, not inferred.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL not in sys.path:
    sys.path.insert(0, SKILL)

from eval import report  # noqa: E402

OLD8 = ["text", "title", "links", "images",
        "metadata", "json_ld", "raw_html", "tables"]
NEW5 = ["date", "iframes", "links_meta", "image_variants", "citations"]
FIELDS13 = OLD8 + NEW5


def _row(fixture, present):
    fields = {f: f in present for f in FIELDS13}
    fields["markdown"] = "markdown" in present
    return {
        "fixture": fixture,
        "page_type": "static",
        "completeness": "complete",
        "completeness_match": True,
        "field_count": sum(1 for f in FIELDS13 if fields[f]),
        # Stored value is the runner's 14-field number; the report must not
        # trust it for the 13-field headline.
        "field_coverage_pct": 100.0 * sum(fields.values()) / 14,
        "fields": fields,
        "elapsed_s": 0.1,
        "text_source": "curl_cffi",
    }


class TestDualCoverageReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "eval_20260101_000000.jsonl")
        rows = [
            # all 8 old, none of the 5 new: headline must drop, old-8 must not
            _row("old_only", OLD8),
            # none of the 8 old, all 5 new: old-8 must stay 0, headline moves
            _row("new_only", NEW5),
            # 4 old + 2 new
            _row("half", ["text", "title", "links", "images", "date", "iframes"]),
        ]
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        patcher = mock.patch.object(report, "RESULTS_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_summary_prints_both_headlines_with_denominators(self):
        out = report.generate_report()
        self.assertIn("| Avg field coverage (13 fields) | 48.7% |", out)
        self.assertIn("| Avg old-8 coverage (8 fields) | 50.0% |", out)

    def test_tool_table_header_names_both_denominators(self):
        out = report.generate_report()
        self.assertIn(
            "| Tool | Coverage (13 fields) | Old-8 coverage (8 fields) |", out)
        self.assertIn("| /crawl | 48.7% | 50.0% |", out)

    def test_every_per_fixture_row_carries_both_numbers(self):
        out = report.generate_report()
        self.assertIn(
            "| Fixture | Type | Completeness | Match | Fields (of 13) | "
            "Coverage (13 fields) | Old-8 coverage (8 fields) | Elapsed | Source |",
            out)
        self.assertIn(
            "| old_only | static | complete | Y | 8/13 | 61.5% | 100.0% |",
            out)
        self.assertIn(
            "| new_only | static | complete | Y | 5/13 | 38.5% | 0.0% |", out)
        self.assertIn(
            "| half | static | complete | Y | 6/13 | 46.2% | 50.0% |", out)

    def test_new_fields_move_headline_not_old8(self):
        """The denominator effect: 5 new fields = up to 38.5 headline points,
        zero old-8 points."""
        out = report.generate_report()
        self.assertIn("61.5% | 100.0%", out)   # old_only
        self.assertIn("38.5% | 0.0%", out)      # new_only


class TestCrawlYield(unittest.TestCase):
    """A6: unique useful pages per 1,000 requests, computed from
    learn/benchmarks/records.jsonl.

    request = one records.jsonl row; useful = learn.py success (no error +
    non-empty text) with a non-empty URL, minus the synthetic stub_ok
    backend that tests/test_contract_properties.py writes into the same
    file; unique = crawl_frontier.canonical_url, the offer() dedup key, so
    utm/trailing-slash aliases count once.
    """

    ROWS = [
        # same canonical page via a utm alias: one unique page, two requests
        {"url": "https://example.com/a?utm_source=x", "success": True,
         "backend_used": "httpx"},
        {"url": "https://example.com/a", "success": True, "backend_used": "httpx"},
        # synthetic contract-test write: not a page
        {"url": "https://example.com/synth", "success": True,
         "backend_used": "stub_ok"},
        # failed request: not useful
        {"url": "https://example.com/dead", "success": False,
         "backend_used": "httpx"},
        {"url": "https://example.com/b", "success": True,
         "backend_used": "curl_cffi"},
        # success with no URL: cannot identify a page
        {"url": "", "success": True, "backend_used": "httpx"},
    ]

    def _records_file(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for r in self.ROWS:
            f.write(json.dumps(r) + "\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_counts_requests_useful_unique_and_yield(self):
        y = report.crawl_yield(self.ROWS)
        self.assertEqual(y["requests"], 6)
        self.assertEqual(y["successful"], 5)
        self.assertEqual(y["synthetic_rows"], 1)
        self.assertEqual(y["useful_requests"], 3)
        self.assertEqual(y["unique_pages"], 2)
        self.assertEqual(y["yield_per_1000"], 333.3)

    def test_yield_row_prints_in_the_scorecard(self):
        eval_dir = tempfile.TemporaryDirectory()
        self.addCleanup(eval_dir.cleanup)
        with open(os.path.join(eval_dir.name, "eval_20260101_000000.jsonl"), "w") as f:
            f.write(json.dumps(_row("static_page", ["text"])) + "\n")
        with mock.patch.object(report, "RESULTS_DIR", eval_dir.name), \
                mock.patch.object(report, "RECORDS_FILE", self._records_file()):
            out = report.generate_report()
        self.assertIn(
            "| Crawl yield (unique useful pages / 1,000 requests) | 333.3 |", out)


class TestCompletenessMetrics(unittest.TestCase):
    """C5: Nimble anchor recall and raw/useful funnel, plus TRACE
    category-weighted scores, 95% CI and exact McNemar, over our score
    rows. The anchor set is the fixture's gated verbatim SENTINELS
    strings; useful is judged only where that ground truth exists.
    """

    def test_anchor_recall_is_passed_over_gated_verbatim_anchors(self):
        self.assertEqual(report.anchor_recall(
            {"sentinel_gated": 4, "sentinel_passed": 3}), 0.75)
        self.assertIsNone(report.anchor_recall(
            {"sentinel_gated": 0, "sentinel_passed": 0}))

    def test_raw_access_and_useful_are_distinct_judgments(self):
        full = {"has_error": False, "completeness": "complete",
                "sentinel_gated": 2, "sentinel_passed": 2}
        self.assertTrue(report.raw_ok(full))
        self.assertTrue(report.access_ok(full))
        self.assertTrue(report.useful_ok(full))
        # fetched fine, but the body is a barrier: raw yes, access no
        barrier = dict(full, completeness="blocked")
        self.assertTrue(report.raw_ok(barrier))
        self.assertFalse(report.access_ok(barrier))
        self.assertFalse(report.useful_ok(barrier))
        errored = dict(full, has_error=True)
        self.assertFalse(report.raw_ok(errored))
        self.assertFalse(report.useful_ok(errored))
        thin = dict(full, sentinel_passed=0)        # recall 0.0 < 0.3
        self.assertFalse(report.useful_ok(thin))
        edge = dict(full, sentinel_gated=10, sentinel_passed=3)  # exactly 0.3
        self.assertTrue(report.useful_ok(edge))
        ungraded = {"has_error": False, "completeness": "complete",
                    "sentinel_gated": 0, "sentinel_passed": 0}
        self.assertIsNone(report.useful_ok(ungraded))

    def test_category_weighted_rate_and_ci_follow_trace(self):
        rows = [
            {"page_type": "x", "hit": True},
            {"page_type": "x", "hit": True},
            {"page_type": "x", "hit": False},
            {"page_type": "y", "hit": False},
        ]
        s = report.category_weighted(rows, lambda r: r["hit"])
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["hits"], 2)
        self.assertEqual(s["pct"], 50.0)                     # micro
        self.assertAlmostEqual(s["rate"], 1 / 3, places=6)    # (2/3 + 0)/2
        # TRACE report.ts: 1.96 * sqrt(sum_c p_c(1-p_c)/n_c) / K;
        # (2/3 * 1/3)/3 + 0 = 0.0741, so 1.96 * sqrt / 2 = 0.2667
        self.assertAlmostEqual(s["ci"], 0.2667, places=3)
        self.assertEqual([c["category"] for c in s["categories"]], ["x", "y"])

    def test_mcnemar_is_trace_two_sided_exact_binomial(self):
        # TRACE analysis.ts: 2 * sum_{i<=min(b,c)} C(n, i) / 2**n
        self.assertEqual(report.mcnemar_p(0, 0), 1.0)
        self.assertEqual(report.mcnemar_p(1, 0), 1.0)
        self.assertEqual(report.mcnemar_p(2, 0), 0.5)
        self.assertEqual(report.mcnemar_p(3, 0), 0.25)
        self.assertEqual(report.mcnemar_p(5, 0), 0.0625)
        self.assertAlmostEqual(report.mcnemar_p(3, 1), 0.625)
        self.assertAlmostEqual(report.mcnemar_p(10, 2), 2 * 79 / 4096)

    def test_completeness_summary_splits_and_pairs_shared_graded(self):
        rows = [
            {"fixture": "f1", "page_type": "a", "has_error": False,
             "completeness": "complete", "sentinel_gated": 2,
             "sentinel_passed": 2},
            {"fixture": "f2", "page_type": "a", "has_error": False,
             "completeness": "complete", "sentinel_gated": 2,
             "sentinel_passed": 0},
            {"fixture": "f3", "page_type": "b", "has_error": False,
             "completeness": "blocked", "sentinel_gated": 2,
             "sentinel_passed": 2},
            {"fixture": "f4", "page_type": "b", "has_error": False,
             "completeness": "complete", "sentinel_gated": 0,
             "sentinel_passed": 0},
        ]
        s = report.completeness_summary(rows)
        self.assertEqual((s["anchors_total"], s["anchors_passed"]), (6, 4))
        self.assertEqual(s["anchor_recall_pct"], 66.7)
        self.assertEqual((s["raw"]["hits"], s["raw"]["n"]), (4, 4))
        self.assertEqual((s["access"]["hits"], s["access"]["n"]), (3, 4))
        self.assertAlmostEqual(s["access"]["rate"], 0.75)
        # useful is graded only: f1 (recall 1.0), f2 (0.0), f3 (no access)
        self.assertEqual((s["useful"]["hits"], s["useful"]["n"]), (1, 3))
        self.assertAlmostEqual(s["useful"]["rate"], 0.25)
        self.assertEqual(s["graded_n"], 3)
        self.assertEqual(s["ungraded_n"], 1)
        # shared graded: f2 is the only discordant pair (access yes,
        # useful no). useful implies access, so c is 0 by construction.
        self.assertEqual(s["mcnemar"], {"b": 1, "c": 0, "p": 1.0, "shared": 3})

    def test_summary_degrades_to_n_a_without_fixtures_or_anchors(self):
        s = report.completeness_summary([])
        self.assertIsNone(s["anchor_recall_pct"])
        self.assertIsNone(s["raw"]["rate"])
        self.assertIsNone(s["useful"]["rate"])
        self.assertEqual(s["mcnemar"], {"b": 0, "c": 0, "p": 1.0, "shared": 0})


class TestPublishedScorecard(unittest.TestCase):
    """D4: the published scorecard splits access from useful and carries
    the 95% CI and the paired test, instead of one blended rate."""

    def _fixture(self, name, page_type, completeness, gated, passed, match):
        row = _row(name, ["text", "title", "links"])
        row.update({"page_type": page_type, "completeness": completeness,
                    "completeness_match": match, "has_error": False,
                    "sentinel_gated": gated, "sentinel_passed": passed})
        return row

    def _report(self, rows):
        """Scorecard over synthetic rows; no dependence on runtime data."""
        out_dir = tempfile.TemporaryDirectory()
        self.addCleanup(out_dir.cleanup)
        path = os.path.join(out_dir.name, "eval_20260101_000000.jsonl")
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        with mock.patch.object(report, "RESULTS_DIR", out_dir.name), \
                mock.patch.object(report, "RECORDS_FILE",
                                  os.path.join(out_dir.name, "absent.jsonl")):
            return report.generate_report()

    def setUp(self):
        # Two article fixtures with verbatim anchors (one thin), one
        # blocked docs fixture, one clean ungraded docs fixture.
        self.rows = [
            self._fixture("f1", "article", "complete", 2, 2, True),
            self._fixture("f2", "article", "complete", 2, 0, True),
            self._fixture("f3", "docs", "blocked", 0, 0, False),
            self._fixture("f4", "docs", "complete", 0, 0, True),
        ]

    def test_scorecard_rows_carry_split_ci_and_paired_test(self):
        out = self._report(self.rows)
        for row in (
            "| Verbatim anchor recall | 50.0% (2/4 strings) |",
            "| Access success (category-weighted) | 75.0% ±34.6 pp "
            "(95% CI; 3/4 fixtures across 2 types) |",
            "| Useful success (category-weighted) | 50.0% ±69.3 pp "
            "(95% CI; 1/2 graded; 2 ungraded) |",
            "| Fetch funnel (raw -> access -> useful) | "
            "100.0% (4/4) -> 75.0% (3/4) -> 50.0% (1/2) |",
            "| Access vs useful, paired (exact McNemar) | "
            "p = 1.000 (1 vs 0 discordant on 2 shared graded) |",
        ):
            self.assertIn(row, out)

    def test_scorecard_degrades_to_n_a_without_graded_fixtures(self):
        out = self._report([dict(self.rows[3])])
        self.assertIn("| Verbatim anchor recall | n/a (no gated anchors) |",
                      out)
        self.assertIn("| Useful success (category-weighted) | "
                      "n/a (no graded fixtures) |", out)


if __name__ == "__main__":
    unittest.main()
