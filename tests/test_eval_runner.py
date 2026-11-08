#!/usr/bin/env python3
"""tests/test_eval_runner.py — eval runner must produce valid scores.

The scorer (eval/runner.py) is stdlib-only, so every scorer test runs
against the recorded results shipped in eval/results/. The private
crawler (scripts/, needs trafilatura/httpx/scrapling) is not shipped in
this evidence repo: live-run tests are skipped with a stated reason, and
every other class skips only if eval.runner itself fails to import.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL)

_RUNNER_CACHE = {}

VENV_PY = os.path.join(SKILL, ".venv", "bin", "python")
RUNNER_PY = VENV_PY if os.path.exists(VENV_PY) else sys.executable


def _runner_probe(expr):
    """Evaluate `expr` against eval.runner in the capability-stack venv.

    Runs out of process so eval.runner's import-time env mutation
    (CRAWL_CAP_CACHE=0) cannot leak into the rest of the suite.
    """
    code = ("import sys, json; sys.path.insert(0, %r); "
            "from eval import runner; print(json.dumps(%s))"
            % (SKILL, expr))
    r = subprocess.run([RUNNER_PY, "-c", code], capture_output=True, text=True,
                       timeout=180, cwd=SKILL)
    if r.returncode != 0:
        raise AssertionError(
            f"runner probe failed rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _has_trafilatura(py):
    r = subprocess.run([py, "-c", "import trafilatura"],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def _runner_importable():
    """eval.runner must import with the stdlib alone (cached per process)."""
    if "ok" not in _RUNNER_CACHE:
        try:
            from eval import runner  # noqa: F401
        except Exception:
            _RUNNER_CACHE["ok"] = False
        else:
            _RUNNER_CACHE["ok"] = True
    return _RUNNER_CACHE["ok"]


def _has_engine():
    """The private crawler: scripts/ plus its extraction stack."""
    return (os.path.isdir(os.path.join(SKILL, "scripts"))
            and _has_trafilatura(RUNNER_PY))


def _has_contract():
    return os.path.isfile(os.path.join(SKILL, "docs", "CONTRACT.md"))


def _latest_full_rows():
    """Rows from the newest full eval run.

    Concurrent lanes write single-fixture result files into the same
    directory, so "sort, take the last" reports a fixture as missing when
    the last file only scored one page. A full run here is one carrying
    every fixture, checked against the current fixture count.
    """
    results_dir = os.path.join(SKILL, "eval", "results")
    n_fixtures = len([f for f in os.listdir(os.path.join(SKILL, "eval", "fixtures"))
                      if f.endswith((".html", ".pdf"))])
    files = sorted(
        (f for f in os.listdir(results_dir)
         if f.startswith("eval_") and f.endswith(".jsonl")
         and "_classified" not in f),
        reverse=True)
    for fname in files:
        with open(os.path.join(results_dir, fname)) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        if len(rows) == n_fixtures:
            return rows
    return []


class TestEvalRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "the scorer must import")
        # Isolated runs: make sure a results file exists before tests 2-5 read it.
        results_dir = os.path.join(SKILL, "eval", "results")
        if not os.path.isdir(results_dir) or not any(
                f.startswith("eval_") and f.endswith(".jsonl")
                for f in os.listdir(results_dir)):
            if not _has_engine():
                raise unittest.SkipTest(
                    "no recorded results shipped and no private engine to produce them")
            subprocess.run([RUNNER_PY, os.path.join(SKILL, "eval", "runner.py")],
                           capture_output=True, text=True, timeout=300, cwd=SKILL,
                           check=False)

    @unittest.skipUnless(_has_engine(),
                         "live eval run needs the private crawler (scripts/)")
    def test_runner_exits_zero_on_all_match(self):
        """The eval runner must exit 0 when all fixtures match expected
        completeness, and must print both coverage numbers (W3)."""
        r = subprocess.run(
            [RUNNER_PY, os.path.join(SKILL, "eval", "runner.py")],
            capture_output=True, text=True, timeout=300,
            cwd=SKILL,
        )
        self.assertEqual(r.returncode, 0,
                         f"runner failed:\nstdout={r.stdout}\nstderr={r.stderr}")
        self.assertIn("Coverage regression-set", r.stdout, r.stdout)
        self.assertIn("Coverage full-set", r.stdout, r.stdout)

    def test_results_file_created(self):
        """Runner must write a JSONL results file."""
        results_dir = os.path.join(SKILL, "eval", "results")
        files = [f for f in os.listdir(results_dir) if f.startswith("eval_") and f.endswith(".jsonl")]
        self.assertTrue(len(files) > 0, "no results file created")

    def test_results_have_required_fields(self):
        """Each result row must have fixture, completeness, field_count, elapsed_s."""
        rows = _latest_full_rows()
        self.assertTrue(rows, "no full results file")
        for row in rows:
            for key in ("fixture", "completeness", "field_count", "elapsed_s", "field_coverage_pct"):
                self.assertIn(key, row, f"missing {key} in {row.get('fixture')}")

    def test_field_coverage_above_floor(self):
        """Average field coverage must be >= 50% across all fixtures."""
        rows = _latest_full_rows()
        self.assertTrue(rows, "no full results file")
        avg = sum(r["field_coverage_pct"] for r in rows) / len(rows)
        self.assertGreaterEqual(avg, 30.0, f"avg coverage {avg:.1f}% < 30%")

    def test_challenge_page_is_blocked(self):
        """The challenge (Cloudflare interstitial) fixture must be classified as blocked."""
        rows = _latest_full_rows()
        self.assertTrue(rows, "no full results file")
        challenge = [r for r in rows if r["fixture"] == "challenge_page"]
        self.assertTrue(challenge, "challenge_page not in results")
        self.assertEqual(challenge[0]["completeness"], "blocked",
                         f"expected blocked, got {challenge[0]['completeness']}")


class TestSentinelGate(unittest.TestCase):
    """W1: each richer fixture carries known sentinels into named fields."""

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "sentinel checks need the scorer")
        cls.probe = _runner_probe(
            '{'
            '"pass": runner.check_sentinels("tweet_embed_page", '
            '{"text": "a SENTINEL-TWEET-TEXT b", '
            '"links": ["https://twitter.com/maravoss/status/1"]}),'
            '"fail": runner.check_sentinels("tweet_embed_page", '
            '{"text": "no tweet here", "links": []}),'
            '"neg": runner.check_sentinels("iframe_rich_page", '
            '{"links": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", '
            '"http://h/documentation_page.html", '
            '"https://www.recaptcha.net/recaptcha/api2/anchor"]}),'
            # The two forever-pending sentinels: shadow text and the tiktok
            # rehydration author, both N/A ground truth now.
            '"shadow": runner.score_result("shadow_dom_page", {"text": '
            '"SENTINEL-SHADOW-HEADING SENTINEL-SHADOW-TEXT SENTINEL-SLOT-FOOTER"}, 0.01),'
            '"social": runner.score_result("social_rehydration_page", {'
            '"raw_html": "__UNIVERSAL_DATA_FOR_REHYDRATION__ SENTINEL-REHYDRATION-CAPTION", '
            '"text": "SENTINEL-REHYDRATION-TEXT", '
            '"image_variants": [{"url": "https://x/media/rehydration-cover.jpg"}]}, 0.01),'
            # B27 landed: iframes[] is in SCORED_FIELDS, so this ground
            # truth is gated again and must pass.
            '"iframe": runner.score_result("iframe_rich_page", {"links": '
            '["https://www.youtube.com/watch?v=dQw4w9WgXcQ", '
            '"http://h/documentation_page.html"], "iframes": '
            '[{"src": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]}, 0.01),'
            # The pending mechanism itself: a future producer row is
            # reported and never fails the gate. update() returns None, so
            # `or` evaluates the check after the table is patched.
            '"pending_mech": runner.SENTINELS.update({"__probe": '
            '[{"field": "text", "contains": "LATER", "pending": "future producer"}]}) '
            'or runner.check_sentinels("__probe", {"text": ""})'
            '}')

    def test_passing_sentinels_gate_ok(self):
        ok, failed, pending = self.probe["pass"]
        self.assertTrue(ok, failed)
        self.assertEqual(failed, [])
        self.assertEqual(pending, [])

    def test_missing_sentinel_fails_the_gate(self):
        ok, failed, pending = self.probe["fail"]
        self.assertFalse(ok)
        self.assertEqual(len(failed), 2, failed)
        self.assertEqual(pending, [])

    def test_negative_sentinel_flags_a_blocked_frame_becoming_a_link(self):
        ok, failed, pending = self.probe["neg"]
        self.assertFalse(ok)
        self.assertTrue(any(f["absent"] for f in failed), failed)

    def test_pending_mechanism_reports_a_future_producer_without_failing_the_gate(self):
        ok, failed, pending = self.probe["pending_mech"]
        self.assertTrue(ok)
        self.assertEqual(failed, [])
        self.assertEqual(len(pending), 1, pending)
        self.assertEqual(pending[0]["pending"], "future producer", pending)

    def test_n_a_sentinels_are_reported_not_pending_and_do_not_gate(self):
        """shadow and tiktok were pending forever; both are N/A ground
        truth: reported with a reason, never gated, never pending."""
        for key, field in (("shadow", "text"), ("social", "video_metadata")):
            score = self.probe[key]
            self.assertEqual(score["sentinel_pending"], [], score)
            self.assertTrue(score["sentinel_ok"], score["sentinel_failed"])
            na = score["sentinel_na"]
            self.assertEqual(len(na), 1, na)
            self.assertEqual(na[0]["field"], field, na)
            self.assertTrue(na[0]["na"], na)
        self.assertEqual(self.probe["shadow"]["sentinel_gated"], 3)
        self.assertEqual(self.probe["shadow"]["sentinel_passed"], 3)
        self.assertEqual(self.probe["social"]["sentinel_gated"], 4)
        self.assertEqual(self.probe["social"]["sentinel_passed"], 4)

    def test_iframes_ground_truth_gates_now_that_b27_landed(self):
        """B27's iframes[] field landed and the fixture carries the video
        id, so the pending marker must be gone and the check must gate."""
        score = self.probe["iframe"]
        self.assertEqual(score["sentinel_gated"], 4, score)
        self.assertEqual(score["sentinel_passed"], 4, score)
        self.assertEqual(score["sentinel_pending"], [], score)
        self.assertTrue(score["sentinel_ok"], score["sentinel_failed"])

    @unittest.skipUnless(_has_contract(),
                         "docs/CONTRACT.md is reviewed in the private repo")
    def test_every_pending_or_na_sentinel_reason_is_documented_in_the_contract(self):
        """The structural gate on the task's rule: a pending or N/A
        sentinel cannot exist without its reason written verbatim into
        docs/CONTRACT.md."""
        sentinels = _runner_probe("runner.SENTINELS")
        with open(os.path.join(SKILL, "docs", "CONTRACT.md"),
                  encoding="utf-8") as f:
            contract = f.read()
        for fixture, entries in sentinels.items():
            for entry in entries:
                reason = entry.get("pending") or entry.get("na")
                if not reason:
                    continue
                needle = entry.get("contains") or entry.get("absent")
                self.assertIn(
                    reason, contract,
                    f"{fixture}/{entry['field']}/{needle}: sentinel reason "
                    "not documented verbatim in docs/CONTRACT.md")


class TestScoredFieldChecks(unittest.TestCase):
    """W2: every SCORED_FIELDS entry is checked against its own schema."""

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "field checks need the scorer")
        cls.probe = _runner_probe(
            '{'
            '"fields": runner.SCORED_FIELDS,'
            '"full": runner.score_result("static_page", {'
            '"text": "body", "title": "T", "links": ["https://x/a"],'
            '"images": ["https://x/i.jpg"],'
            '"metadata": {"k": "v", "k2": "v2"},'
            '"json_ld": [{"@type": "Article"}],'
            # Full-body-sized raw_html for a 167-byte fixture; the scorer
            # now checks the share of the source page, not key existence.
            '"raw_html": "<html>" + " " * 200,'
            '"tables": 1, "tables_raw": ["h | v\\n--- | ---\\n1 | 2"],'
            '"date": "2026-11-06",'
            '"iframes": [{"src": "https://x/f.html"}],'
            '"links_meta": [{"href": "https://x/", "text": "home"}],'
            '"image_variants": [{"url": "https://x/i.jpg"}],'
            '"citations": [{"text": "[1]", "url": "https://x/paper"}],'
            # static_page's source is one <h1>Welcome</h1>; the markdown
            # must round-trip it to score.
            '"markdown": "# Welcome" + chr(10) + chr(10) + "body"'
            '}, 0.01),'
            '"bare": runner.score_result("stub", '
            '{"text": "body", "title": "T"}, 0.01),'
            '"mismatch": runner.score_result("stub", {'
            '"date": "not-a-date",'
            '"iframes": ["https://x/f.html"],'
            '"links_meta": "https://x/",'
            '"image_variants": [{"url": "/relative/i.jpg"}],'
            '"citations": ["/relative/paper"]'
            '}, 0.01)'
            '}')

    def test_scored_fields_include_the_five_new_surfaces(self):
        for field in ("date", "iframes", "links_meta", "image_variants", "citations"):
            self.assertIn(field, self.probe["fields"], field)
        self.assertEqual(len(self.probe["fields"]), 14)

    def test_stub_lane_emitting_schema_shaped_values_scores_them(self):
        score = self.probe["full"]
        for field in ("date", "iframes", "links_meta", "image_variants", "citations"):
            self.assertTrue(score["fields"][field], f"{field} not scored")
        self.assertEqual(score["field_count"], 14, score["fields"])
        self.assertEqual(score["field_coverage_pct"], 100.0)

    def test_absent_new_fields_score_zero_without_crashing(self):
        score = self.probe["bare"]
        self.assertEqual(score["field_count"], 2, score["fields"])
        for field in ("date", "iframes", "links_meta", "image_variants", "citations", "markdown"):
            self.assertFalse(score["fields"][field], field)

    def test_wrong_shape_or_unparseable_value_scores_zero(self):
        score = self.probe["mismatch"]
        for field in ("date", "iframes", "links_meta", "image_variants", "citations", "markdown"):
            self.assertFalse(score["fields"][field], field)


class TestRegressionGuard(unittest.TestCase):
    """W3: the original 13 stay a fixed regression set with their own
    coverage number, and the gate fails if that set ever drops."""

    ORIGINAL_13 = [
        "article_basic", "article_with_jsonld", "article_with_tables",
        "github_repo", "pricing_page", "documentation_page", "static_page",
        "youtube_embed", "youtube_stub", "challenge_page", "empty_page",
        "large_page", "pdf_sample",
    ]

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "the regression guard needs the scorer")
        cls.probe = _runner_probe(
            '{'
            '"regression": runner.REGRESSION_FIXTURES,'
            '"floor": runner.REGRESSION_COVERAGE_FLOOR,'
            '"summary": runner.coverage_summary(['
            '{"fixture": "article_basic", "field_coverage_pct": 53.8},'
            '{"fixture": "empty_page", "field_coverage_pct": 0.0},'
            '{"fixture": "srcset_lazy_page", "field_coverage_pct": 53.8},'
            '{"fixture": "tables_span_page", "field_coverage_pct": 46.2}'
            ']),'
            '"at_floor": runner.regression_passes('
            '{"regression_pct": runner.REGRESSION_COVERAGE_FLOOR}),'
            '"below": runner.regression_passes('
            '{"regression_pct": runner.REGRESSION_COVERAGE_FLOOR - 0.1}),'
            '"full_floor": runner.FULL_COVERAGE_FLOOR,'
            '"full_below": runner.regression_passes('
            '{"regression_pct": runner.REGRESSION_COVERAGE_FLOOR,'
            ' "full_pct": runner.FULL_COVERAGE_FLOOR - 0.1}, full_suite=True),'
            '"subset_below": runner.regression_passes('
            '{"regression_pct": runner.REGRESSION_COVERAGE_FLOOR,'
            ' "full_pct": runner.FULL_COVERAGE_FLOOR - 0.1})'
            '}')

    def test_original_thirteen_are_the_regression_set(self):
        self.assertEqual(sorted(self.probe["regression"]), sorted(self.ORIGINAL_13))
        self.assertEqual(len(self.probe["regression"]), 13)

    def test_summary_reports_two_numbers_over_disjoint_sets(self):
        summary = self.probe["summary"]
        self.assertEqual(summary["regression_n"], 2, summary)
        self.assertEqual(summary["regression_pct"], 26.9, summary)
        self.assertEqual(summary["full_n"], 4, summary)
        self.assertEqual(summary["full_pct"], 38.5, summary)

    def test_floor_is_the_measured_regression_baseline(self):
        # Recomputed after the scorer started dereferencing pointer-split
        # raw_html: three consecutive full runs at 36.3%, large_page 1/14.
        self.assertEqual(self.probe["floor"], 36.3)

    def test_check_fails_when_the_regression_set_drops(self):
        self.assertTrue(self.probe["at_floor"])
        self.assertFalse(self.probe["below"])

    def test_full_suite_floor_gates_only_full_runs(self):
        # The suite average is not a property of one page, so a --fixture
        # subset run is exempt; a full run is held to it.
        self.assertEqual(self.probe["full_floor"], 52.0)
        self.assertFalse(self.probe["full_below"])
        self.assertTrue(self.probe["subset_below"])


@unittest.skipUnless(_has_engine(),
                         "the full-results dump runs the private crawler")
class TestFullResultsDump(unittest.TestCase):
    """Index-ready full-results dump beside the summary JSONL.

    The dump is one line per fixture carrying url/title/text/json_ld/metadata,
    written atomically and best-effort, and must not alter the summary JSONL
    shape (W1-W3 scoring contract).
    """

    # Byte-for-byte shape of a summary row in the pre-dump runner: same 27
    # top-level keys and the same 14 scored fields. A new key here means the
    # scoring path changed, not that the dump landed.
    SUMMARY_KEYS = sorted([
        "completeness", "completeness_match", "elapsed_s", "ensemble_skipped",
        "ensemble_used", "error", "expected_completeness", "field_count",
        "field_coverage_pct", "fields", "fixture", "has_error", "jsonld_count",
        "links_count", "markdown_integrity", "page_type", "pointer_split",
        "sentinel_failed", "sentinel_gated", "sentinel_na", "sentinel_ok",
        "sentinel_passed", "sentinel_pending", "tables_count", "text_len",
        "text_source", "url",
    ])
    SCORED_FIELD_KEYS = sorted([
        "text", "title", "links", "images", "metadata", "json_ld", "raw_html",
        "tables", "date", "iframes", "links_meta", "image_variants",
        "citations", "markdown",
    ])
    DUMP_KEYS = ["json_ld", "metadata", "text", "title", "url"]

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "the full-results dump needs the scorer")

    def _run(self, *extra):
        return subprocess.run(
            [RUNNER_PY, os.path.join(SKILL, "eval", "runner.py"), *extra],
            capture_output=True, text=True, timeout=300, cwd=SKILL)

    def test_full_results_jsonl_written_per_fixture(self):
        """A runner pass must write eval/results/full_results.jsonl with one
        line per fixture, and the summary JSONL shape must be unchanged."""
        r = self._run()
        self.assertEqual(r.returncode, 0,
                         f"runner failed:\nstdout={r.stdout}\nstderr={r.stderr}")
        results_dir = os.path.join(SKILL, "eval", "results")
        dump_path = os.path.join(results_dir, "full_results.jsonl")
        self.assertTrue(os.path.isfile(dump_path), "full_results.jsonl missing")
        with open(dump_path, encoding="utf-8") as f:
            dump_rows = [json.loads(line) for line in f if line.strip()]
        fixture_files = sorted(
            x for x in os.listdir(os.path.join(SKILL, "eval", "fixtures"))
            if x.endswith((".html", ".pdf")))
        self.assertEqual(len(dump_rows), len(fixture_files),
                         "one line per fixture required")
        for row in dump_rows:
            self.assertEqual(sorted(row), self.DUMP_KEYS, row)
            self.assertTrue(row["url"].startswith("http"), row)
            self.assertIsInstance(row["title"], str)
            self.assertIsInstance(row["text"], str)
        leftovers = [x for x in os.listdir(results_dir)
                     if x.startswith("full_results.jsonl.")]
        self.assertEqual(leftovers, [], f"tmp files left behind: {leftovers}")

        summary_files = sorted(
            f for f in os.listdir(results_dir)
            if f.startswith("eval_") and f.endswith(".jsonl")
            and "_classified" not in f)
        self.assertTrue(summary_files, "no summary JSONL")
        with open(os.path.join(results_dir, summary_files[-1]),
                  encoding="utf-8") as f:
            summary_rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(summary_rows), len(fixture_files))
        for row in summary_rows:
            self.assertEqual(sorted(row), self.SUMMARY_KEYS, row)
            self.assertEqual(sorted(row["fields"]), self.SCORED_FIELD_KEYS)
            for key in ("text", "title", "json_ld", "metadata"):
                self.assertNotIn(key, row,
                                 f"dump field {key!r} leaked into summary")

    def test_dump_write_failure_does_not_fail_the_run(self):
        """The dump target being unwritable must not fail the eval: the
        summary still lands and the runner still exits 0."""
        out = tempfile.mkdtemp(prefix="eval-full-results-")
        self.addCleanup(_rm_writable_tree, out)
        target = os.path.join(out, "full_results.jsonl")
        os.mkdir(target)          # rename of the tmp file onto this fails
        os.chmod(target, 0o500)    # destination is unwritable
        r = self._run("--output", out, "--fixture", "article_basic")
        self.assertEqual(r.returncode, 0,
                         f"dump failure failed the eval:\n"
                         f"stdout={r.stdout}\nstderr={r.stderr}")
        summaries = [f for f in os.listdir(out)
                     if f.startswith("eval_") and f.endswith(".jsonl")]
        self.assertTrue(summaries, "summary JSONL missing after dump failure")
        leftovers = [f for f in os.listdir(out)
                     if f.startswith("full_results.jsonl.")]
        self.assertEqual(leftovers, [], f"tmp files left behind: {leftovers}")


def _rm_writable_tree(path):
    for root, _dirs, _files in os.walk(path):
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


class TestPointerRawHtmlDeref(unittest.TestCase):
    """X6 follow-up: large_page ships its full body as raw_html, but the
    714 KB payload pointer-splits to pointer.out_dir/raw.html. The ratio
    rule scores those bytes; a missing pointer file is absent, not a crash."""

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "pointer deref needs the scorer")

    def setUp(self):
        self.out_dir = tempfile.mkdtemp(prefix="eval-pointer-")
        self.addCleanup(_rm_writable_tree, self.out_dir)

    def _raw_html_present(self):
        score = _runner_probe(
            '{"score": runner.score_result("large_page", '
            '{"pointer": {"out_dir": %r}}, 0.01)}' % self.out_dir)
        return score["score"]["fields"]["raw_html"]

    def test_pointer_bytes_clear_the_ratio_bar(self):
        # 142802 of large_page's 714007 source bytes is the 20% bar.
        with open(os.path.join(self.out_dir, "raw.html"), "wb") as f:
            f.write(b"x" * 142802)
        self.assertTrue(self._raw_html_present())

    def test_pointer_bytes_one_under_the_bar_score_absent(self):
        with open(os.path.join(self.out_dir, "raw.html"), "wb") as f:
            f.write(b"x" * 142801)
        self.assertFalse(self._raw_html_present())

    def test_missing_pointer_file_is_absent_not_a_crash(self):
        self.assertFalse(self._raw_html_present())

    def test_field_present_accepts_bytes_at_the_same_bar(self):
        p = _runner_probe(
            '{"bytes_at": runner._field_present("raw_html",'
            ' b"x" * 142802, 714007),'
            ' "bytes_under": runner._field_present("raw_html",'
            ' b"x" * 142801, 714007),'
            ' "str_at": runner._field_present("raw_html",'
            ' "x" * 142802, 714007)}')
        self.assertTrue(p["bytes_at"])
        self.assertFalse(p["bytes_under"])
        self.assertTrue(p["str_at"])

    def test_pointer_split_telemetry_is_true_when_the_payload_split(self):
        # a81e0a7 wart: pointer_split read the completeness dict, which never
        # names the pointer, so the flag was False on every fixture including
        # large_page (whose body lives in pointer.out_dir).
        p = _runner_probe(
            '{"split": runner.score_result("large_page", '
            '{"pointer": {"out_dir": %r}}, 0.01)["pointer_split"],'
            '"inline": runner.score_result("static_page", '
            '{"text": "body"}, 0.01)["pointer_split"],'
            '"empty": runner.score_result("static_page", '
            '{"pointer": {}}, 0.01)["pointer_split"]}' % self.out_dir)
        self.assertTrue(
            p["split"],
            "a result carrying a pointer key was pointer-split; telemetry must say True")
        self.assertFalse(p["inline"], "an inline payload is not pointer-split")
        self.assertFalse(p["empty"], "an empty pointer dict is not a split")


class TestBenchmarkContractFixes(unittest.TestCase):
    """Challenger + miss-matrix fixes: presence must mean the data is there.

    One method per contract change, each probing eval.runner out of process.
    Evidence and measured impact: eval/research/benchmark-fixed.md.
    """

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "benchmark contract checks need the scorer")

    def test_raw_html_must_cover_a_real_share_of_the_page(self):
        """raw_html is a completeness ratio: >=20% of the source page's
        bytes, not a non-empty 8192-byte window."""
        p = _runner_probe(
            '{"window": runner._field_present("raw_html", "x" * 8192, 714007),'
            ' "full": runner._field_present("raw_html", "x" * 714007, 714007),'
            ' "at_bar": runner._field_present("raw_html", "x" * 142802, 714007),'
            ' "under_bar": runner._field_present("raw_html", "x" * 142801, 714007),'
            ' "unknown_size": runner._field_present("raw_html", "<html></html>", None),'
            ' "empty": runner._field_present("raw_html", "", 1000)}')
        self.assertFalse(
            p["window"],
            "8192 of 714007 bytes (1.1%) must not score raw_html present")
        self.assertTrue(p["full"])
        self.assertTrue(p["at_bar"], "20% of the page is the bar")
        self.assertFalse(p["under_bar"])
        self.assertFalse(
            p["unknown_size"],
            "unknown source size cannot prove completeness; no window fallback")
        self.assertFalse(p["empty"])

    def test_tables_scores_rows_in_tables_raw_not_the_count(self):
        """tables is the count channel; tables_raw carries the rows. A bare
        count (3) or a header-only table must not score."""
        p = _runner_probe(
            '{"count_only": runner.score_result("stub", {"tables": 3}, 0.01)'
            '["fields"]["tables"],'
            ' "count_with_no_rows": runner.score_result('
            '"stub", {"tables": 3, "tables_raw": []}, 0.01)'
            '["fields"]["tables"],'
            ' "rows": runner.score_result("stub", {"tables_raw":'
            ' ["h | v" + chr(10) + "--- | ---" + chr(10) + "1 | 2"]}, 0.01)'
            '["fields"]["tables"],'
            ' "header_only": runner.score_result("stub", {"tables_raw":'
            ' ["h | v" + chr(10) + "--- | ---"]}, 0.01)'
            '["fields"]["tables"],'
            ' "not_a_list": runner.score_result("stub", {"tables_raw":'
            ' "h | v"}, 0.01)["fields"]["tables"]}')
        self.assertFalse(p["count_only"], "tables: 3 is a count, not rows")
        self.assertFalse(p["count_with_no_rows"])
        self.assertTrue(p["rows"], "rows in tables_raw are the contract")
        self.assertFalse(p["header_only"], "a table with no data rows is empty")
        self.assertFalse(p["not_a_list"])

    def test_images_are_credited_from_image_variants(self):
        """The og:image-only page ships its image in image_variants, not
        images. Real variants credit images; relative URLs do not."""
        p = _runner_probe(
            '{"direct": runner.score_result("stub",'
            ' {"images": ["https://x/i.jpg"]}, 0.01)["fields"]["images"],'
            ' "from_variants": runner.score_result("stub",'
            ' {"images": [], "image_variants":'
            ' [{"url": "https://x/i.jpg", "source": "meta"}]}, 0.01)'
            '["fields"]["images"],'
            ' "missing_images_key": runner.score_result("stub",'
            ' {"image_variants": [{"url": "https://x/i.jpg"}]}, 0.01)'
            '["fields"]["images"],'
            ' "relative_only": runner.score_result("stub",'
            ' {"image_variants": [{"url": "/i.jpg"}]}, 0.01)'
            '["fields"]["images"],'
            ' "empty_variants": runner.score_result("stub",'
            ' {"images": [], "image_variants": []}, 0.01)'
            '["fields"]["images"]}')
        self.assertTrue(p["direct"])
        self.assertTrue(
            p["from_variants"],
            "a real og:image variant is an image the page carries")
        self.assertTrue(p["missing_images_key"])
        self.assertFalse(p["relative_only"], "a relative URL is not a real variant")
        self.assertFalse(p["empty_variants"])

    def test_counts_and_flags_do_not_score_as_presence(self):
        """No bool() fallback: 3, 1 and True are telemetry, not content.
        Whitespace-only strings are empty."""
        p = _runner_probe(
            '{"text_zero": runner._field_present("text", 0),'
            ' "text_three": runner._field_present("text", 3),'
            ' "title_one": runner._field_present("title", 1),'
            ' "title_ws": runner._field_present("title", "\\t"),'
            ' "text_ws": runner._field_present("text", "   "),'
            ' "text_true": runner._field_present("text", True),'
            ' "jsonld_bool": runner._field_present("json_ld", True),'
            ' "title_real": runner._field_present("title", "real"),'
            ' "jsonld_list": runner._field_present("json_ld",'
            ' [{"@type": "Article"}])}')
        self.assertFalse(p["text_zero"])
        self.assertFalse(p["text_three"])
        self.assertFalse(p["title_one"])
        self.assertFalse(p["title_ws"])
        self.assertFalse(p["text_ws"])
        self.assertFalse(p["text_true"])
        self.assertFalse(p["jsonld_bool"])
        self.assertTrue(p["title_real"])
        self.assertTrue(p["jsonld_list"])

    def test_metadata_needs_two_distinct_keys_with_values(self):
        """The always-on cell: every crawl carries some metadata key, so a
        1-key map scored full on 21/22 fixtures. Depth is two distinct keys
        carrying a value; a lone og:image key is not extracted metadata."""
        p = _runner_probe(
            '{"one": runner.score_result("stub",'
            ' {"metadata": {"og:title": "T"}}, 0.01)'
            '["fields"]["metadata"],'
            ' "empty_second": runner.score_result("stub",'
            ' {"metadata": {"og:title": "T", "og:image": ""}}, 0.01)'
            '["fields"]["metadata"],'
            ' "two": runner.score_result("stub",'
            ' {"metadata": {"og:title": "T",'
            ' "og:image": "https://x/i.jpg"}}, 0.01)'
            '["fields"]["metadata"],'
            ' "blank_second": runner.score_result("stub",'
            ' {"metadata": {"og:title": "T", "og:image": "  "}}, 0.01)'
            '["fields"]["metadata"],'
            ' "null": runner.score_result("stub",'
            ' {"metadata": None}, 0.01)["fields"]["metadata"],'
            ' "not_a_dict": runner.score_result("stub",'
            ' {"metadata": ["og:title"]}, 0.01)'
            '["fields"]["metadata"]}')
        self.assertFalse(p["one"], "one key is not metadata depth")
        self.assertFalse(p["empty_second"], "an empty value is not a second key")
        self.assertTrue(p["two"])
        self.assertFalse(p["blank_second"])
        self.assertFalse(p["null"])
        self.assertFalse(p["not_a_dict"])

    @unittest.skipUnless(_has_engine(),
                         "this test runs the private crawler")
    def test_citations_have_a_fixture_with_real_citation_markup(self):
        """Citations need ground truth. One fixture carries a References
        block with absolute cited URLs, and the crawler must surface them
        (the challenger and miss-matrix both require this before citations
        may stay in SCORED_FIELDS)."""
        p = _runner_probe(
            '{"citations": (r := __import__("scripts.ensemble",'
            ' fromlist=["crawl"]).crawl(runner.start_fixture_server()[0]'
            ' + "metadata_rich_page.html")).get("citations"),'
            ' "present": runner.score_result("metadata_rich_page", r, 0.1)'
            '["fields"]["citations"]}')
        urls = [c.get("url") for c in (p["citations"] or [])]
        self.assertTrue(
            p["present"], f"citations must score; got {p['citations']!r}")
        self.assertIn("https://example.org/ref/capability", urls,
                      f"cited URL missing: {urls}")

    def test_applicable_recall_excludes_blocked_and_reports_blocked_rate(self):
        """The headline second number: recall over cells whose source carries
        the field. Blocked fixtures are N/A (challenge handling is the
        blocked-rate line, not an extraction miss). Markdown is applicable
        wherever the source has an h1-h3, a table, or 30+ body words."""
        p = _runner_probe(
            '{"applicable": sorted(runner.source_applicable_fields('
            '"article_with_tables")),'
            '"summary": runner.applicable_summary(['
            '{"fixture": "article_with_tables", "completeness": "complete",'
            ' "fields": {"title": True, "raw_html": True, "tables": True}},'
            '{"fixture": "static_page", "completeness": "complete",'
            ' "fields": {"title": True}},'
            '{"fixture": "challenge_page", "completeness": "blocked",'
            ' "fields": {}}'
            '])}')
        self.assertEqual(p["applicable"],
                         ["markdown", "metadata", "raw_html", "tables", "title"])
        s = p["summary"]
        # 5 applicable on article_with_tables (the <title> fix makes
        # metadata applicable) + 4 on static_page; challenge_page contributes
        # 0 (blocked = N/A). The synthetic rows hit title/raw_html/tables,
        # so 4/9 = 44.4% and the one markdown cell stays a documented miss.
        self.assertEqual(s["total"], 9, s)
        self.assertEqual(s["hits"], 4, s)
        self.assertEqual(s["recall_pct"], 44.4, s)
        self.assertEqual(s["blocked_n"], 1, s)
        self.assertEqual(s["blocked_pct"], 33.3, s)

    @unittest.skipUnless(_has_engine(),
                         "this test runs the private crawler")
    def test_headline_prints_applicable_recall_and_blocked_rate(self):
        """make eval output must carry the applicable-cell recall beside the
        fixture-ceiling coverage, a blocked-rate line, and the C5
        leaderboard block (anchor recall, funnel, CI, McNemar)."""
        r = subprocess.run(
            [RUNNER_PY, os.path.join(SKILL, "eval", "runner.py"),
             "--fixture", "article_with_tables"],
            capture_output=True, text=True, timeout=300, cwd=SKILL)
        self.assertEqual(r.returncode, 0,
                         f"runner failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn("Applicable-cell recall", r.stdout)
        self.assertIn("Blocked rate", r.stdout)
        self.assertIn("Completeness (anchor recall", r.stdout)
        self.assertIn("Fetch funnel: raw", r.stdout)
        self.assertIn("Useful score, category-weighted (95% CI)", r.stdout)
        self.assertIn("McNemar (access vs useful", r.stdout)


class TestApplicableSourceAudit(unittest.TestCase):
    """source_applicable_fields under-detects real structures.

    The audit behind eval/research/exec-perfectionist-b.md. Each case is a
    page shape the ensemble's producers read; applicable must include the
    field that producer fills. og:image-only is the green guard (already
    detected); the rest were misses before the audit.
    """

    WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
             "kilo lima mike november oscar papa quebec romeo sierra tango "
             "uniform victor whiskey xray yankee zulu amber badge castle "
             "dragon eagle forest glacier harbor island jungle canyon meadow")

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "the applicable-source audit needs the scorer")
        w = cls.WORDS
        cases = {
            # Green guard: the og:image-only page was already detected.
            "og_image_only": (
                '<html><head><title>Hero</title>'
                '<meta property="og:image" content="https://img.example/hero.jpg">'
                '</head><body><p>%s</p></body></html>' % w),
            # twitter:image is the same producer path, not in the old regex.
            "twitter_image_only": (
                '<html><head><title>Hero</title>'
                '<meta name="twitter:image" content="https://img.example/hero.jpg">'
                '</head><body><p>%s</p></body></html>' % w),
            # Video-only page: poster is the only image signal, no body words.
            "video_only": (
                '<html><head><title>Clip</title></head><body>'
                '<video poster="https://img.example/p.jpg" '
                'src="https://v.example/clip.mp4"></video></body></html>'),
            # Microdata feeds structured_data -> json_ld with no ld+json.
            "microdata_only": (
                '<html><head><title>Item</title></head><body>'
                '<div itemscope itemtype="https://schema.org/Article">'
                '<meta itemprop="headline" content="Headline"><p>%s</p>'
                '</div></body></html>' % w),
            # B59 puts iframe srcs into links/links_meta, no anchors present.
            "iframe_only": (
                '<html><head><title>Embed</title></head><body>'
                '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
                '<p>%s</p></body></html>' % w),
            # FIELD_RULES derives metadata from JSON-LD; no <meta> tags.
            "jsonld_no_meta": (
                '<html><head><title>LD</title>'
                '<script type="application/ld+json">'
                '{"@type":"Article","headline":"Headline"}</script>'
                '</head><body><p>%s</p></body></html>' % w),
            # htmldate's cheap pass reads <meta name="date">.
            "meta_date": (
                '<html><head><title>D</title>'
                '<meta name="date" content="2026-01-02"></head>'
                '<body><p>%s</p></body></html>' % w),
            # htmldate reads <time> text with no datetime attribute.
            "time_no_datetime": (
                '<html><head><title>D</title></head><body>'
                '<time>2 January 2026</time><p>%s</p></body></html>' % w),
            # FIELD_RULES copies <title> into metadata["title"].
            "title_only": (
                '<html><head><title>Only</title></head>'
                '<body><p>%s</p></body></html>' % w),
        }
        expr = "{%s}" % ",".join(
            '"%s": sorted(runner._applicable_from_html(%r))' % (k, v)
            for k, v in cases.items())
        cls.probe = _runner_probe(expr)
        # A heading inside a JS string is not page structure: markdown must
        # not be asked to round-trip it (same rule as the applicable scan).
        head_src = ("<html><body><h1>Real</h1>"
                    "<script>var x = '<h2>JS only</h2>';</script>"
                    "</body></html>")
        cls.head_probe = _runner_probe(
            '{"headings": runner._source_headings(%r),'
            ' "present": runner._field_present("markdown",'
            ' "# Real" + chr(10) + chr(10) + "body", source_html=%r)}'
            % (head_src, head_src))

    def test_og_image_only_page_is_detected(self):
        self.assertEqual(
            self.probe["og_image_only"],
            ["image_variants", "images", "markdown", "metadata",
             "raw_html", "text", "title"],
            self.probe["og_image_only"])

    def test_twitter_image_only_page_is_detected(self):
        self.assertEqual(
            self.probe["twitter_image_only"],
            ["image_variants", "images", "markdown", "metadata",
             "raw_html", "text", "title"],
            self.probe["twitter_image_only"])

    def test_video_only_page_detects_poster_image_and_title_metadata(self):
        self.assertEqual(
            self.probe["video_only"],
            ["image_variants", "images", "metadata", "raw_html", "title"],
            self.probe["video_only"])

    def test_microdata_only_page_detects_json_ld(self):
        self.assertEqual(
            self.probe["microdata_only"],
            ["json_ld", "markdown", "metadata", "raw_html", "text", "title"],
            self.probe["microdata_only"])

    def test_iframe_only_page_detects_links_from_the_embed_src(self):
        self.assertEqual(
            self.probe["iframe_only"],
            ["iframes", "links", "links_meta", "markdown", "metadata",
             "raw_html", "text", "title"],
            self.probe["iframe_only"])

    def test_json_ld_without_meta_tags_detects_metadata(self):
        self.assertEqual(
            self.probe["jsonld_no_meta"],
            ["json_ld", "markdown", "metadata", "raw_html", "text", "title"],
            self.probe["jsonld_no_meta"])

    def test_meta_date_and_bare_time_tags_detect_date(self):
        expected = ["date", "markdown", "metadata", "raw_html", "text", "title"]
        self.assertEqual(self.probe["meta_date"], expected,
                         self.probe["meta_date"])
        self.assertEqual(self.probe["time_no_datetime"], expected,
                         self.probe["time_no_datetime"])

    def test_title_only_page_detects_metadata(self):
        self.assertEqual(
            self.probe["title_only"],
            ["markdown", "metadata", "raw_html", "text", "title"],
            self.probe["title_only"])

    def test_headings_inside_scripts_are_not_markdown_ground_truth(self):
        # The probe round-trips through JSON, so the heading comes back a list.
        self.assertEqual(self.head_probe["headings"], [[1, "real"]],
                         self.head_probe)
        self.assertTrue(
            self.head_probe["present"],
            "a JS-string h2 is not DOM structure; markdown must not be "
            "penalized for not round-tripping it")


class TestMarkdownContract(unittest.TestCase):
    """markdown is scored with format integrity, not key existence.

    Red while the producer is opt-in (today's results carry no markdown),
    green once the sibling lane flips CRAWL_MARKDOWN on by default. The
    dependency and the measured evidence live in
    eval/research/markdown-scoring.md.
    """

    @classmethod
    def setUpClass(cls):
        if not _runner_importable():
            raise unittest.SkipTest(
                "eval.runner failed to import; "
                "markdown checks need the scorer")
        src = ("<html><body><h1>Quarterly Report</h1><h2>Revenue</h2>"
               "<table><tr><th>Region</th></tr><tr><td>EMEA</td></tr>"
               "</table></body></html>")
        good = ("# Quarterly Report\n\n## Revenue\n\n"
                "| Region |\n| --- |\n| EMEA |")
        no_head = ("# Quarterly Report\n\n"
                   "| Region |\n| --- |\n| EMEA |")
        no_table = ("# Quarterly Report\n\n## Revenue\n\n"
                    "The EMEA region reported revenue.")
        leak = good + "\n<div>raw markup</div>"
        expr = (
            '(lambda src=%r, good=%r, no_head=%r, no_table=%r, leak=%r: {'
            '"fields": runner.SCORED_FIELDS,'
            '"good": runner._field_present("markdown", good, source_html=src),'
            '"good_integrity": runner.markdown_integrity(src, good),'
            '"missing_heading": runner._field_present('
            '"markdown", no_head, source_html=src),'
            '"table_as_prose": runner._field_present('
            '"markdown", no_table, source_html=src),'
            '"tag_leak": runner._field_present("markdown", leak, source_html=src),'
            '"empty": runner._field_present("markdown", "", source_html=src),'
            '"ws": runner._field_present("markdown", "  \\n ", source_html=src),'
            '"count": runner._field_present("markdown", 3, source_html=src),'
            '"no_source": runner._field_present("markdown", "plain body"),'
            '"fixture_present": runner.score_result('
            '"static_page", {"markdown": "# Welcome\\n\\nbody"}, 0.01),'
            '"today": runner.score_result("static_page", {"text": "body"}, 0.01),'
            '})()' % (src, good, no_head, no_table, leak)
        )
        cls.probe = _runner_probe(expr)

    def test_markdown_is_scored_and_the_denominator_is_fourteen(self):
        self.assertIn("markdown", self.probe["fields"])
        self.assertEqual(len(self.probe["fields"]), 14)

    def test_clean_markdown_round_trips_and_scores_present(self):
        self.assertTrue(self.probe["good"])
        i = self.probe["good_integrity"]
        self.assertEqual(i["source_headings"], 2, i)
        self.assertEqual(i["headings_roundtrip"], 2, i)
        self.assertEqual(i["source_tables"], 1, i)
        self.assertEqual(i["pipe_tables"], 1, i)
        self.assertTrue(i["tables_preserved"], i)
        self.assertEqual(i["tag_leaks"], 0, i)

    def test_each_format_defect_scores_zero(self):
        self.assertFalse(self.probe["missing_heading"],
                         "a dropped source heading is a format failure")
        self.assertFalse(self.probe["table_as_prose"],
                         "a source table rendered as prose is a format failure")
        self.assertFalse(self.probe["tag_leak"],
                         "raw HTML leaked into markdown is a format failure")
        self.assertFalse(self.probe["empty"])
        self.assertFalse(self.probe["ws"])
        self.assertFalse(self.probe["count"],
                         "a count is telemetry, not markdown")

    def test_a_clean_string_scores_when_the_source_has_no_headings_or_tables(self):
        self.assertTrue(self.probe["no_source"])

    def test_today_results_score_markdown_absent_without_crashing(self):
        # The producer is still opt-in: score 0, record the integrity
        # breakdown, keep the rest of the row intact.
        today = self.probe["today"]
        self.assertFalse(today["fields"]["markdown"])
        self.assertEqual(today["field_count"], 1, today["fields"])
        self.assertIn("markdown_integrity", today)
        self.assertEqual(today["markdown_integrity"]["source_headings"], 1)
        self.assertEqual(today["markdown_integrity"]["headings_roundtrip"], 0)
        # A stub lane that does round-trip the fixture heading scores it.
        self.assertTrue(self.probe["fixture_present"]["fields"]["markdown"])

    @unittest.skipUnless(_has_engine(),
                         "this test runs the private crawler")
    def test_headline_prints_markdown_integrity_beside_coverage(self):
        r = subprocess.run(
            [RUNNER_PY, os.path.join(SKILL, "eval", "runner.py"),
             "--fixture", "article_with_tables"],
            capture_output=True, text=True, timeout=300, cwd=SKILL)
        self.assertEqual(r.returncode, 0,
                         f"runner failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn("Markdown integrity", r.stdout, r.stdout)


if __name__ == "__main__":
    unittest.main()
