#!/usr/bin/env python3
"""eval/runner.py — exhaustive capability eval for /crawl.

Runs ensemble.crawl() against every fixture, scores completeness, field
coverage, and latency. Writes JSONL results to eval/results/.

Usage:
    python3 eval/runner.py [--fixture NAME] [--output DIR] [--json]

A live run needs the private crawler (scripts/), imported in main() only.
Everything else here is stdlib, so recorded results can be re-scored
offline. EXPECTED_COMPLETENESS and SENTINELS below are the EXPECTED data
the fixtures are held to.

Runs offline against a localhost fixture server. Measured completeness
baseline is GREEN for the current ensemble (2026-11-06); raise an
EXPECTED_COMPLETENESS entry only when the ensemble genuinely improves,
then eval/track.py records the delta.
"""
import argparse
import html
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from datetime import date as _date, datetime as _datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_DIR)

# The runner must not read or write the shared capability disk cache:
# a stale cache written by a different interpreter (e.g. system python3
# without the crawl deps) reports every backend missing and blocks the
# whole eval. Probe the current interpreter's real capabilities instead.
os.environ["CRAWL_CAP_CACHE"] = "0"

from eval import report  # noqa: E402

FIXTURES_DIR = os.path.join(SKILL_DIR, "eval", "fixtures")
RESULTS_DIR = os.path.join(SKILL_DIR, "eval", "results")

# Index-ready dump next to the summary JSONL; see write_full_results.
FULL_RESULTS_NAME = "full_results.jsonl"

# Fields the eval checks for presence (non-empty), each against its own
# schema shape (see _field_present). W2 expanded the surface from 8 to 13
# once the side-channel producers landed in the ensemble contract; the
# markdown lane appended the LLM-consumed artifact as 14.
SCORED_FIELDS = [
    "text", "title", "links", "images", "metadata",
    "json_ld", "raw_html", "tables",
    "date", "iframes", "links_meta", "image_variants", "citations",
    "markdown",
]

_DATE_PRECISION_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")

# markdown format integrity (challenger change 5, failures item 1).
_MD_SOURCE_HEADING_RE = re.compile(
    r"<(h[1-3])\b[^>]*>(.*?)</\1>", re.S | re.I)
_MD_ATX_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_MD_TABLE_DELIM_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?\s*$")
_MD_RAW_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s+[^<>]*?)?/?>")
_MD_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_MD_FENCE_RE = re.compile(
    r"(?ms)^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^(?P=fence)[ \t]*$")
_MD_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_MD_SPACE_RE = re.compile(r"\s+")

# raw_html is scored as completeness, not presence: the returned body must
# cover at least this share of the source page's bytes. An 8192-byte window
# on a 714007-byte page is 1.1% and must not score. See docs/CONTRACT.md
# section 12 and eval/research/benchmark-fixed.md.
RAW_HTML_MIN_RATIO = 0.20


def _abs_http_url(value):
    """True for an absolute http(s) URL string."""
    if not isinstance(value, str):
        return False
    try:
        parts = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _parseable_date(value):
    """True for a non-empty date string at year, month, or day precision,
    or a full ISO datetime. Garbage scores 0."""
    if not isinstance(value, str) or not value.strip():
        return False
    s = value.strip()
    m = _DATE_PRECISION_RE.match(s)
    if m:
        try:
            _date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
            return True
        except ValueError:
            return False
    try:
        _datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _list_of_url_records(value, keys):
    """Non-empty list carrying at least one record shaped like the schema:
    a dict (or [text, title, href] style sequence) with an absolute URL
    under one of `keys`. One valid record proves the surface exists; a
    uniform list of strings, relative-only URLs, or a scalar scores 0."""
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if isinstance(item, dict):
            if any(_abs_http_url(item.get(k)) for k in keys):
                return True
        elif isinstance(item, (list, tuple)):
            if any(_abs_http_url(x) for x in item):
                return True
    return False


def _fixture_bytes(fixture_name):
    """Byte size of the fixture's source page, or None when it is not on
    disk (unit probes, non-suite callers). raw_html completeness cannot be
    computed without it."""
    for ext in (".html", ".pdf"):
        path = os.path.join(FIXTURES_DIR, fixture_name + ext)
        if os.path.isfile(path):
            return os.path.getsize(path)
    return None


def _pointer_field_bytes(pointer, name):
    """Bytes of a pointer-split field file, or None when the pointer or
    the file is missing. A stale pointer is absence, not a crash."""
    if not isinstance(pointer, dict):
        return None
    out_dir = pointer.get("out_dir")
    if not out_dir:
        return None
    try:
        with open(os.path.join(str(out_dir), name), "rb") as f:
            return f.read()
    except OSError:
        return None


def _merge_pointer_manifest(result):
    """X6b: a pointer-split payload's inline body is a stub; the manifest
    (pointer.out_dir/result.json) carries the real field set. Merge the
    manifest's concrete values over the stub so scoring reads what the
    crawl actually produced. Missing manifest = unchanged result, never a
    crash."""
    pointer = result.get("pointer")
    if not isinstance(pointer, dict) or not pointer.get("out_dir"):
        return result
    try:
        with open(os.path.join(str(pointer["out_dir"]), "result.json"),
                  encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return result
    merged = dict(result)
    for k, v in manifest.items():
        if v not in (None, "", [], {}) and not merged.get(k):
            merged[k] = v
    return merged


def _tables_have_rows(value):
    """tables_raw is a list of pipe-canonicalized markdown tables: a header,
    a delimiter, then data rows. The `tables` integer count is telemetry and
    never presence; a header-only table has no rows to score."""
    if not isinstance(value, list):
        return False
    return any(
        isinstance(t, str)
        and len([ln for ln in t.splitlines() if ln.strip()]) >= 3
        for t in value
    )


def _metadata_has_depth(value):
    """Metadata counts as extracted when at least two distinct keys carry a
    value. The always-on cell came from a single always-emitted key
    (og:image on the rich page), which is extraction noise, not depth."""
    if not isinstance(value, dict):
        return False
    filled = 0
    for v in value.values():
        if isinstance(v, str):
            if v.strip():
                filled += 1
        elif v is not None:
            filled += 1
    return filled >= 2


def _md_text(value):
    """Normalize heading text so an HTML heading and its markdown ATX
    counterpart compare equal across entities, tags and whitespace."""
    return _MD_SPACE_RE.sub(
        " ", html.unescape(_MD_TAG_STRIP_RE.sub(" ", value))).strip().lower()


def _md_without_fences(markdown):
    """Fenced code is not page structure: its sample headings, pipe-looking
    lines and <div> strings are not round-trips or leaks."""
    return _MD_FENCE_RE.sub("", markdown)


def _source_headings(source_html):
    """(level, text) for every h1-h3 in the fixture source.

    A heading inside a JS string is not DOM structure, so it is not markdown
    ground truth (same rule as _applicable_from_html's script strip).
    """
    source = re.sub(r"<script\b[^>]*>.*?</script>", " ", source_html or "",
                    flags=re.S | re.I)
    return [(int(m.group(1)[1]), _md_text(m.group(2)))
            for m in _MD_SOURCE_HEADING_RE.finditer(source)]


def _markdown_headings(markdown):
    """(level, text) for every ATX h1-h3 outside fenced code."""
    headings = []
    for line in _md_without_fences(markdown).splitlines():
        m = _MD_ATX_HEADING_RE.match(line)
        if m:
            headings.append((len(m.group(1)), _md_text(m.group(2))))
    return headings


def _pipe_table_count(markdown):
    """Number of GFM table blocks: each has one delimiter row of dashes
    and pipes. A bare `---` is a horizontal rule, not a table."""
    return sum(1 for line in _md_without_fences(markdown).splitlines()
               if "|" in line and _MD_TABLE_DELIM_RE.match(line))


def _md_tag_leaks(markdown):
    """Raw HTML tags and comments surviving into markdown, outside code."""
    body = _md_without_fences(markdown)
    return len(_MD_RAW_TAG_RE.findall(body)) + len(_MD_COMMENT_RE.findall(body))


def markdown_integrity(source_html, markdown):
    """Format integrity of one markdown string against its source page.

    Headings round-trip when every source h1-h3 reappears as an ATX
    heading at the same level. Tables are preserved when a source <table>
    becomes at least one GFM pipe block. tag_leaks counts raw HTML tags
    and comments that survived conversion. An unknown source (stub lane,
    PDF) can only be checked for leaks, so its heading/table expectation
    is vacuous and presence falls back to non-empty plus clean.
    """
    markdown = markdown if isinstance(markdown, str) else ""
    source = source_html or ""
    src_h = _source_headings(source)
    md_h = _markdown_headings(markdown)
    matched = sum(1 for h in src_h if h in md_h)
    src_t = len(re.findall(r"<table\b", source, re.I))
    pipes = _pipe_table_count(markdown)
    leaks = _md_tag_leaks(markdown)
    return {
        "source_headings": len(src_h),
        "headings_roundtrip": matched,
        "headings_pct": round(100.0 * matched / len(src_h), 1) if src_h else 100.0,
        "source_tables": src_t,
        "pipe_tables": pipes,
        "tables_preserved": src_t == 0 or pipes >= src_t,
        "tag_leaks": leaks,
        "tag_leak_free": leaks == 0,
    }


def _markdown_present(source_html, markdown):
    """Presence is the integrity bar, not a non-empty-string check: the
    challenger rejects scoring markdown on presence alone."""
    if not isinstance(markdown, str) or not markdown.strip():
        return False
    integrity = markdown_integrity(source_html, markdown)
    return (integrity["headings_roundtrip"] == integrity["source_headings"]
            and integrity["tables_preserved"]
            and integrity["tag_leak_free"])


def _fixture_source_html(fixture_name):
    """Source markup for an HTML fixture, or None for PDFs and stub lanes."""
    path = os.path.join(FIXTURES_DIR, fixture_name + ".html")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _field_present(field, value, page_bytes=None, source_html=None):
    """Per-field presence check. raw_html is a completeness ratio against
    the source page, markdown is a format-integrity check against the
    source markup; the side channels are validated against the schema
    shape the ensemble contract promises. Absence or a wrong shape scores
    0 without raising."""
    if field == "date":
        return _parseable_date(value)
    if field == "markdown":
        # Headings/tables round-trip and no raw tags: the integrity bar,
        # not a non-empty-string check.
        return _markdown_present(source_html, value)
    if field == "raw_html":
        if not isinstance(value, (str, bytes)) or not value:
            return False
        # Documented exception: no source size means the ratio cannot be
        # computed. Score 0 instead of falling back to a byte window.
        return (page_bytes is not None
                and len(value) >= RAW_HTML_MIN_RATIO * page_bytes)
    if field == "tables":
        # Score the rows channel (tables_raw), never the tables count.
        return _tables_have_rows(value)
    if field == "metadata":
        # Depth is two distinct keys; a lone always-on key is not a win.
        return _metadata_has_depth(value)
    if field == "iframes":
        # B27: iframes[] is a list of dicts with an absolute src.
        return _list_of_url_records(value, ("src",))
    if field == "links_meta":
        # B4: anchor text beside an absolute href/url.
        return _list_of_url_records(value, ("href", "url", "src"))
    if field == "image_variants":
        # B2: one record per source class, url absolute.
        return _list_of_url_records(value, ("url", "src"))
    if field == "citations":
        # B3: citation record carrying the cited absolute url.
        return _list_of_url_records(value, ("url", "href"))
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return False  # a count or flag is telemetry, never extracted content
    if isinstance(value, (list, dict)):
        return len(value) > 0
    if isinstance(value, str):
        return bool(value.strip())
    return False

# W3: the original 13 fixtures are a permanent regression set. The richer
# batch must never weaken the gate they define, so they are named explicitly
# and scored on their own line.
REGRESSION_FIXTURES = [
    "article_basic", "article_with_jsonld", "article_with_tables",
    "github_repo", "pricing_page", "documentation_page", "static_page",
    "youtube_embed", "youtube_stub", "challenge_page", "empty_page",
    "large_page", "pdf_sample",
]
# Measured 2026-11-07 with this scorer after the pointer deref landed:
# three consecutive full runs at 36.3% with identical per-fixture field
# counts, large_page 1/14 on raw_html read from pointer.out_dir/raw.html.
# Before the deref the same runs measured 35.7% and large_page scored 0/14.
# The four markdown-absent fixtures (empty_page, pdf_sample, large_page,
# shadow_dom_page) are honest 0s; see eval/research/markdown-scoring.md.
# The gate fails if the set ever drops below the measured baseline.
REGRESSION_COVERAGE_FLOOR = 36.3
# Full-suite floor (measured 2026-11-07, 25 fixtures): mean
# field_coverage_pct over every fixture, including the 3 full-surface
# pages. The original-13 floor above stays the W3 baseline; this one
# extends the gate to the richer batch and the full-surface pages, which
# previously had only the sentinel and completeness lines to hold them.
FULL_COVERAGE_FLOOR = 52.0

# Expected completeness level per fixture name (measured baseline 2026-11-06).
EXPECTED_COMPLETENESS = {
    "article_basic": "complete",
    "article_with_tables": "complete",
    "article_with_jsonld": "complete",
    "github_repo": "complete",
    "pricing_page": "partial",
    "documentation_page": "partial",
    "static_page": "complete",
    "youtube_embed": "complete",
    "youtube_stub": "complete",
    "challenge_page": "blocked",
    "empty_page": "blocked",
    "large_page": "complete",
    "pdf_sample": "partial",
    # W1 richer batch (measured 2026-11-07): every new page is a static
    # HTML page the ensemble resolves as complete.
    "srcset_lazy_page": "complete",
    "tables_span_page": "complete",
    "jsonld_graph_microdata": "complete",
    "tweet_embed_page": "complete",
    "shadow_dom_page": "complete",
    "social_rehydration_page": "complete",
    "iframe_rich_page": "complete",
    "metadata_rich_page": "complete",
    # B5: the render is recovered by scrapling_stealthy's gap-fill lane
    # (the only lane that runs JS); the declared [data-ready] target is
    # the wait contract for runs that set CRAWL_WAIT_SELECTOR.
    "spa_delay_page": "complete",
    # PIECE 1 full-surface batch (measured 2026-11-07): each page carries
    # every scored field in source markup and the ensemble resolves it
    # complete.
    "full_surface_product": "complete",
    "full_surface_news": "complete",
    "full_surface_docs": "complete",
    # C2 batch (measured 2026-11-08): nine pages resolve complete; the
    # soft-404 resolves blocked, which is the honest read of a page whose
    # only content is a not-found state.
    "shadow_dom_ops_page": "complete",
    "shadow_dom_nested_page": "complete",
    "table_heavy_report_page": "complete",
    "paginated_jobs_page": "complete",
    "soft_404_page": "blocked",
    "late_hydration_spa_page": "complete",
    "template_modal_page": "complete",
    "pdf_linked_filing_page": "complete",
    "nested_iframe_dashboard_page": "complete",
    "wikipedia_longform_page": "complete",
}

# Known sentinel strings the richer fixtures must surface, and the field
# that must carry them.
#   gated (default): a miss fails the eval.
#   {"pending": reason}: producer row has not landed yet; reported, not
#     gated. The reason must be documented verbatim in docs/CONTRACT.md.
#   {"na": reason}: N/A ground truth for this fixture; reported, not gated,
#     never pending. Same documentation rule. See CONTRACT.md section 12.
SENTINELS = {
    "srcset_lazy_page": [
        {"field": "text", "contains": "SENTINEL-SRCSET-LAZY"},
        {"field": "images", "contains": "hero-640.jpg"},
        {"field": "image_variants", "contains": "hero-1600.webp"},
        {"field": "image_variants", "contains": "lazy-photo-1200.jpg"},
        {"field": "image_variants", "contains": "picture-1600.avif"},
        {"field": "image_variants", "contains": "poster-frame.jpg"},
    ],
    "tables_span_page": [
        {"field": "text", "contains": "SENTINEL-TABLES-SPAN"},
        {"field": "tables_raw", "contains": "EMEA"},
        {"field": "tables_raw", "contains": "Rotterdam"},
        {"field": "tables_raw", "contains": "Engineering"},
    ],
    "jsonld_graph_microdata": [
        {"field": "json_ld", "contains": "SENTINEL-GRAPH-ARTICLE"},
        {"field": "json_ld", "contains": "SENTINEL-GRAPH-AUTHOR"},
        {"field": "json_ld", "contains": "SENTINEL-MICRODATA-SKU"},
        {"field": "date", "contains": "2026-05-17"},
    ],
    # HTML path only. The syndication/oEmbed twitter_metadata lane needs
    # live HTTP against twitter.com and is its own future row.
    "tweet_embed_page": [
        {"field": "text", "contains": "SENTINEL-TWEET-TEXT"},
        {"field": "links", "contains": "twitter.com/maravoss/status/"},
    ],
    "shadow_dom_page": [
        {"field": "text", "contains": "SENTINEL-SHADOW-HEADING"},
        {"field": "text", "contains": "SENTINEL-SHADOW-TEXT"},
        {"field": "text", "contains": "SENTINEL-SLOT-FOOTER"},
        # N/A, not pending: the runtime root is never in the bytes this eval
        # fetches, and no lane that pierces it runs for a static page.
        {"field": "text", "contains": "SENTINEL-JS-SHADOW-TEXT",
         "na": "SENTINEL-JS-SHADOW-TEXT exists only in a runtime attachShadow root; "
               "the fetched bytes never carry it and no lane pierces runtime "
               "shadow roots for this fixture"},
    ],
    # The island rides raw_html (the first 8 KB) and the visible fallback
    # rides text. The author/counters half is live-HTTP work only.
    "social_rehydration_page": [
        {"field": "raw_html", "contains": "__UNIVERSAL_DATA_FOR_REHYDRATION__"},
        {"field": "raw_html", "contains": "SENTINEL-REHYDRATION-CAPTION"},
        {"field": "text", "contains": "SENTINEL-REHYDRATION-TEXT"},
        {"field": "image_variants", "contains": "rehydration-cover.jpg"},
        {"field": "video_metadata", "contains": "SENTINEL-REHYDRATION-AUTHOR",
         "na": "video_metadata is N/A for this fixture: B26's lane self-gates to "
               "known video hosts (tiktok.com) and the shaped payload exposes no "
               "video_metadata key"},
    ],
    # Negative sentinel: the reCAPTCHA frame must never become a link.
    "iframe_rich_page": [
        {"field": "links", "contains": "youtube.com/watch?v=dQw4w9WgXcQ"},
        {"field": "links", "contains": "documentation_page.html"},
        {"field": "links", "absent": "recaptcha"},
        # B27 landed: iframes[] is in SCORED_FIELDS and the frame carries
        # the video id, so this ground truth gates again.
        {"field": "iframes", "contains": "dQw4w9WgXcQ"},
    ],
    "metadata_rich_page": [
        {"field": "metadata", "contains": "SENTINEL-META-ARRAY-ONE"},
        {"field": "metadata", "contains": "SENTINEL-META-ARRAY-TWO"},
        {"field": "metadata", "contains": "SENTINEL-META-CITATION"},
        {"field": "metadata", "contains": "SENTINEL-META-DC"},
        {"field": "date", "contains": "2026-04-02"},
        # Ground truth for citations (challenger/miss-matrix F3): a real
        # References block the citation producer must surface.
        {"field": "citations", "contains": "https://example.org/ref/capability"},
    ],
    # B5: the delayed body only exists after the client timer; the
    # renderer lane must bring it back into `text`.
    "spa_delay_page": [
        {"field": "text", "contains": "SENTINEL-SPA-DELAY"},
    ],
    # PIECE 1 full-surface pages: one sentinel per scored field, each
    # riding the structure that produces that field. Live-crawl verified
    # 42/42 green 2026-11-07.
    "full_surface_product": [
        {"field": "title", "contains": "SENTINEL-PRODUCT-TITLE"},
        {"field": "text", "contains": "SENTINEL-PRODUCT-TEXT"},
        {"field": "links", "contains": "SENTINEL-PRODUCT-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-PRODUCT-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-PRODUCT-IMG"},
        {"field": "metadata", "contains": "SENTINEL-PRODUCT-META"},
        {"field": "json_ld", "contains": "SENTINEL-PRODUCT-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-PRODUCT-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-PRODUCT-TABLE"},
        {"field": "date", "contains": "2026-09-02"},
        {"field": "iframes", "contains": "SENTINEL-PRODUCT-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-PRODUCT-ALT"},
        {"field": "citations", "contains": "SENTINEL-PRODUCT-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-PRODUCT-MARKDOWN"},
    ],
    "full_surface_news": [
        {"field": "title", "contains": "SENTINEL-NEWS-TITLE"},
        {"field": "text", "contains": "SENTINEL-NEWS-TEXT"},
        {"field": "links", "contains": "SENTINEL-NEWS-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-NEWS-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-NEWS-IMG"},
        {"field": "metadata", "contains": "SENTINEL-NEWS-META"},
        {"field": "json_ld", "contains": "SENTINEL-NEWS-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-NEWS-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-NEWS-TABLE"},
        {"field": "date", "contains": "2026-10-22"},
        {"field": "iframes", "contains": "SENTINEL-NEWS-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-NEWS-ALT"},
        {"field": "citations", "contains": "SENTINEL-NEWS-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-NEWS-MARKDOWN"},
    ],
    "full_surface_docs": [
        {"field": "title", "contains": "SENTINEL-DOCS-TITLE"},
        {"field": "text", "contains": "SENTINEL-DOCS-TEXT"},
        {"field": "links", "contains": "SENTINEL-DOCS-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-DOCS-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-DOCS-IMG"},
        {"field": "metadata", "contains": "SENTINEL-DOCS-META"},
        {"field": "json_ld", "contains": "SENTINEL-DOCS-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-DOCS-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-DOCS-TABLE"},
        {"field": "date", "contains": "2026-10-30"},
        {"field": "iframes", "contains": "SENTINEL-DOCS-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-DOCS-ALT"},
        {"field": "citations", "contains": "SENTINEL-DOCS-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-DOCS-MARKDOWN"},
    ],
    # C2 batch: one sentinel per scored field, each riding the structure
    # that produces it. Measured live 2026-11-08; see
    # eval/research/exec-c2-fixtures.md for per-fixture rationale.
    "shadow_dom_ops_page": [
        {"field": "title", "contains": "SENTINEL-OPS-TITLE"},
        {"field": "text", "contains": "SENTINEL-OPS-TEXT"},
        {"field": "links", "contains": "SENTINEL-OPS-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-OPS-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-OPS-IMG"},
        {"field": "metadata", "contains": "SENTINEL-OPS-META"},
        {"field": "json_ld", "contains": "SENTINEL-OPS-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-OPS-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-OPS-TABLE"},
        {"field": "date", "contains": "2026-11-05"},
        {"field": "iframes", "contains": "SENTINEL-OPS-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-OPS-ALT"},
        {"field": "citations", "contains": "SENTINEL-OPS-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-OPS-MARKDOWN"},
        # The dwell widget is assembled at runtime; the bytes never carry it.
        {"field": "text", "contains": "SENTINEL-OPS-SHADOW-WIDGET",
         "na": "SENTINEL-OPS-SHADOW-WIDGET exists only in a runtime attachShadow root; the fetched bytes never carry it and no lane pierces runtime shadow roots for this fixture"},
    ],
    "shadow_dom_nested_page": [
        {"field": "title", "contains": "SENTINEL-NEST-TITLE"},
        # The outer root's own paragraph, not the light-DOM lede.
        {"field": "text", "contains": "SENTINEL-NEST-SHADOW-TEXT"},
        {"field": "links", "contains": "SENTINEL-NEST-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-NEST-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-NEST-IMG"},
        {"field": "metadata", "contains": "SENTINEL-NEST-META"},
        {"field": "json_ld", "contains": "SENTINEL-NEST-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-NEST-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-NEST-TABLE"},
        {"field": "date", "contains": "2026-10-28"},
        {"field": "iframes", "contains": "SENTINEL-NEST-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-NEST-ALT"},
        {"field": "citations", "contains": "SENTINEL-NEST-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-NEST-MARKDOWN"},
        # Second runtime root, same N/A ground truth as shadow_dom_page.
        {"field": "text", "contains": "SENTINEL-NEST-SHADOW-NOTE",
         "na": "SENTINEL-NEST-SHADOW-NOTE exists only in a runtime attachShadow root; the fetched bytes never carry it and no lane pierces runtime shadow roots for this fixture"},
    ],
    "table_heavy_report_page": [
        {"field": "title", "contains": "SENTINEL-LEDGER-TITLE"},
        {"field": "text", "contains": "SENTINEL-LEDGER-TEXT"},
        {"field": "links", "contains": "SENTINEL-LEDGER-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-LEDGER-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-LEDGER-IMG"},
        {"field": "metadata", "contains": "SENTINEL-LEDGER-META"},
        {"field": "json_ld", "contains": "SENTINEL-LEDGER-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-LEDGER-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-LEDGER-TABLE"},
        {"field": "date", "contains": "2026-10-31"},
        {"field": "iframes", "contains": "SENTINEL-LEDGER-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-LEDGER-ALT"},
        {"field": "citations", "contains": "SENTINEL-LEDGER-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-LEDGER-MARKDOWN"},
    ],
    "paginated_jobs_page": [
        {"field": "title", "contains": "SENTINEL-JOBS-TITLE"},
        {"field": "text", "contains": "SENTINEL-JOBS-TEXT"},
        {"field": "links", "contains": "SENTINEL-JOBS-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-JOBS-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-JOBS-IMG"},
        {"field": "metadata", "contains": "SENTINEL-JOBS-META"},
        {"field": "json_ld", "contains": "SENTINEL-JOBS-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-JOBS-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-JOBS-TABLE"},
        {"field": "date", "contains": "2026-11-03"},
        {"field": "iframes", "contains": "SENTINEL-JOBS-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-JOBS-ALT"},
        {"field": "citations", "contains": "SENTINEL-JOBS-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-JOBS-MARKDOWN"},
    ],
    # No date signal, no frame, no references block: 11 applicable cells.
    "soft_404_page": [
        {"field": "title", "contains": "SENTINEL-MISS-TITLE"},
        {"field": "text", "contains": "SENTINEL-MISS-TEXT"},
        {"field": "links", "contains": "SENTINEL-MISS-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-MISS-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-MISS-IMG"},
        {"field": "metadata", "contains": "SENTINEL-MISS-META"},
        {"field": "json_ld", "contains": "SENTINEL-MISS-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-MISS-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-MISS-TABLE"},
        {"field": "image_variants", "contains": "SENTINEL-MISS-ALT"},
        {"field": "markdown", "contains": "SENTINEL-MISS-MARKDOWN"},
    ],
    "late_hydration_spa_page": [
        {"field": "title", "contains": "SENTINEL-HYD-TITLE"},
        {"field": "text", "contains": "SENTINEL-HYD-TEXT"},
        {"field": "links", "contains": "SENTINEL-HYD-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-HYD-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-HYD-IMG"},
        {"field": "metadata", "contains": "SENTINEL-HYD-META"},
        {"field": "json_ld", "contains": "SENTINEL-HYD-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-HYD-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-HYD-TABLE"},
        {"field": "date", "contains": "2026-11-06"},
        {"field": "iframes", "contains": "SENTINEL-HYD-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-HYD-ALT"},
        {"field": "citations", "contains": "SENTINEL-HYD-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-HYD-MARKDOWN"},
    ],
    "template_modal_page": [
        {"field": "title", "contains": "SENTINEL-MODAL-TITLE"},
        {"field": "text", "contains": "SENTINEL-MODAL-TEXT"},
        {"field": "links", "contains": "SENTINEL-MODAL-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-MODAL-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-MODAL-IMG"},
        {"field": "metadata", "contains": "SENTINEL-MODAL-META"},
        {"field": "json_ld", "contains": "SENTINEL-MODAL-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-MODAL-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-MODAL-TABLE"},
        {"field": "date", "contains": "2026-11-04"},
        {"field": "iframes", "contains": "SENTINEL-MODAL-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-MODAL-ALT"},
        {"field": "citations", "contains": "SENTINEL-MODAL-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-MODAL-MARKDOWN"},
    ],
    "pdf_linked_filing_page": [
        {"field": "title", "contains": "SENTINEL-FILING-TITLE"},
        {"field": "text", "contains": "SENTINEL-FILING-TEXT"},
        {"field": "links", "contains": "SENTINEL-FILING-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-FILING-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-FILING-IMG"},
        {"field": "metadata", "contains": "SENTINEL-FILING-META"},
        {"field": "json_ld", "contains": "SENTINEL-FILING-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-FILING-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-FILING-TABLE"},
        {"field": "date", "contains": "2026-11-01"},
        {"field": "iframes", "contains": "SENTINEL-FILING-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-FILING-ALT"},
        {"field": "citations", "contains": "SENTINEL-FILING-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-FILING-MARKDOWN"},
    ],
    "nested_iframe_dashboard_page": [
        {"field": "title", "contains": "SENTINEL-DASH-TITLE"},
        {"field": "text", "contains": "SENTINEL-DASH-TEXT"},
        {"field": "links", "contains": "SENTINEL-DASH-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-DASH-LINKS-META"},
        {"field": "images", "contains": "SENTINEL-DASH-IMG"},
        {"field": "metadata", "contains": "SENTINEL-DASH-META"},
        {"field": "json_ld", "contains": "SENTINEL-DASH-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-DASH-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-DASH-TABLE"},
        {"field": "date", "contains": "2026-11-05"},
        {"field": "iframes", "contains": "SENTINEL-DASH-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-DASH-ALT"},
        {"field": "citations", "contains": "SENTINEL-DASH-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-DASH-MARKDOWN"},
    ],
    "wikipedia_longform_page": [
        {"field": "title", "contains": "SENTINEL-ATLAS-TITLE"},
        {"field": "text", "contains": "SENTINEL-ATLAS-TEXT"},
        {"field": "links", "contains": "SENTINEL-ATLAS-LINK"},
        {"field": "links_meta", "contains": "SENTINEL-ATLAS-LINKS-META"},
        # og:image-only head + srcset body: images rides image_variants.
        {"field": "images", "contains": "SENTINEL-ATLAS-IMG"},
        {"field": "metadata", "contains": "SENTINEL-ATLAS-META"},
        {"field": "json_ld", "contains": "SENTINEL-ATLAS-JSONLD"},
        {"field": "raw_html", "contains": "SENTINEL-ATLAS-RAWHTML"},
        {"field": "tables", "contains": "SENTINEL-ATLAS-TABLE"},
        {"field": "date", "contains": "2026-10-27"},
        {"field": "iframes", "contains": "SENTINEL-ATLAS-IFRAME"},
        {"field": "image_variants", "contains": "SENTINEL-ATLAS-ALT"},
        {"field": "citations", "contains": "SENTINEL-ATLAS-CITATION"},
        {"field": "markdown", "contains": "SENTINEL-ATLAS-MARKDOWN"},
    ],
}


def _sentinel_haystack(field, result):
    """Field value as searchable text; containers become their JSON.

    Resolves the same value score_result scores: `tables` from its rows
    channel tables_raw, and `images` from image_variants when the flat
    images channel is empty (the og:image-only contract).
    """
    if field == "tables":
        v = result.get("tables_raw")
    elif field == "images" and not result.get("images"):
        v = result.get("image_variants")
    else:
        v = result.get(field)
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        return str(v)


def check_sentinels(fixture_name, result):
    """Evaluate SENTINELS[fixture_name] against one result.

    Returns (ok, failed, pending). Failed entries gate the exit code;
    pending entries are reported until their producer row lands. Entries
    carrying `na` are skipped entirely: N/A ground truth is reported on the
    score row (sentinel_na) with a reason documented in docs/CONTRACT.md.
    """
    failed, pending = [], []
    for entry in SENTINELS.get(fixture_name, []):
        if entry.get("na"):
            continue
        needle = entry.get("absent") if "absent" in entry else entry.get("contains")
        found = needle in _sentinel_haystack(entry["field"], result)
        hit = (not found) if "absent" in entry else found
        if hit:
            continue
        rec = {"field": entry["field"], "needle": needle,
               "absent": "absent" in entry}
        if entry.get("pending"):
            rec["pending"] = entry["pending"]
            pending.append(rec)
        else:
            failed.append(rec)
    return not failed, failed, pending


def score_result(fixture_name, result, elapsed_s):
    """Score a single ensemble result. Returns a dict of metrics."""
    # X6b: pointer-split payloads score from their manifest, not the stub.
    result = _merge_pointer_manifest(result)
    score = {
        "fixture": fixture_name,
        "page_type": result.get("page_type"),
        "completeness": (result.get("completeness") or {}).get("level"),
        "expected_completeness": EXPECTED_COMPLETENESS.get(fixture_name),
        "completeness_match": (result.get("completeness") or {}).get("level")
            == EXPECTED_COMPLETENESS.get(fixture_name),
        "text_source": result.get("text_source"),
        "elapsed_s": round(elapsed_s, 3),
        "error": result.get("error"),
        "has_error": "error" in result,
        "fields": {},
        "field_count": 0,
        "ensemble_used": result.get("ensemble_used", []),
        "ensemble_skipped": result.get("ensemble_skipped", []),
        # _shape emits a `pointer` key only when the payload exceeded the
        # return budget; completeness never names the split, so the pointer
        # key itself is the telemetry source.
        "pointer_split": bool(result.get("pointer")),
    }
    page_bytes = _fixture_bytes(fixture_name)
    source_html = _fixture_source_html(fixture_name)
    for field in SCORED_FIELDS:
        # tables is scored from its data channel, tables_raw, not the count.
        value = (result.get("tables_raw") if field == "tables"
                 else result.get(field))
        if field == "raw_html" and not value:
            # X6: an over-budget payload pointer-splits, so the full body
            # lives at pointer.out_dir/raw.html and the key is absent here.
            value = _pointer_field_bytes(result.get("pointer"), "raw.html")
        score["fields"][field] = _field_present(
            field, value, page_bytes, source_html)
        if field == "images" and not score["fields"][field]:
            # The og:image-only page carries its image in image_variants.
            score["fields"][field] = _list_of_url_records(
                result.get("image_variants"), ("url", "src"))
        if score["fields"][field]:
            score["field_count"] += 1
    score["field_coverage_pct"] = round(
        100.0 * score["field_count"] / len(SCORED_FIELDS), 1
    )
    # W4: the integrity breakdown ships with the row so the printed
    # aggregate and a reader of the JSONL agree.
    score["markdown_integrity"] = markdown_integrity(
        source_html, result.get("markdown"))
    # Text length
    text = result.get("text", "") or ""
    score["text_len"] = len(text)
    # Links count
    links = result.get("links", []) or []
    score["links_count"] = len(links)
    # Tables count (the ensemble may emit a count instead of a list)
    tables = result.get("tables", []) or []
    score["tables_count"] = (len(tables) if isinstance(tables, (list, dict))
                             else int(tables))
    # JSON-LD count
    jld = result.get("json_ld", []) or []
    score["jsonld_count"] = len(jld) if isinstance(jld, list) else (1 if jld else 0)
    # W1: known-sentinel assertions per fixture (pending and na entries do
    # not gate; na reasons are documented in docs/CONTRACT.md section 12).
    ok, failed, pending = check_sentinels(fixture_name, result)
    entries = SENTINELS.get(fixture_name, [])
    gated = [e for e in entries
             if not e.get("pending") and not e.get("na")]
    score["sentinel_ok"] = ok
    score["sentinel_failed"] = failed
    score["sentinel_pending"] = pending
    score["sentinel_na"] = [
        {"field": e["field"],
         "needle": e.get("contains") or e.get("absent"), "na": e["na"]}
        for e in entries if e.get("na")
    ]
    score["sentinel_gated"] = len(gated)
    score["sentinel_passed"] = len(gated) - len(failed)
    return score


def source_applicable_fields(fixture_name):
    """Which scored fields the fixture's source actually carries.

    Source-side scan (miss-matrix F1, challenger change 1): the markup that
    produces each field, plus a 30-word floor for text. Heuristic and it
    under-detects, so this is an applicable floor, not ground truth; a field
    with no detectable source signal is not applicable and is never counted
    as a miss. PDFs are read with pypdf. See
    eval/research/benchmark-fixed.md.
    """
    path = None
    for ext in (".html", ".pdf"):
        candidate = os.path.join(FIXTURES_DIR, fixture_name + ext)
        if os.path.isfile(candidate):
            path = candidate
            break
    if path is None:
        return set()
    with open(path, "rb") as f:
        raw = f.read()

    if path.endswith(".pdf"):
        fields = set()
        try:
            import io
            import logging
            from pypdf import PdfReader
            logging.getLogger("pypdf").setLevel(logging.ERROR)
            reader = PdfReader(io.BytesIO(raw))
            body = " ".join((page.extract_text() or "") for page in reader.pages)
            if len(re.findall(r"[A-Za-z]{2,}", body)) >= 30:
                fields.add("text")
            title = (reader.metadata or {}).get("/Title") or ""
            if str(title).strip():
                fields.add("title")
        except Exception:
            pass
        return fields

    return _applicable_from_html(raw.decode("utf-8", "replace"))


def _applicable_from_html(src):
    """Which scored fields the html string itself carries (source-side
    scan, miss-matrix F1). Heuristic floor, not ground truth; the text
    floor is over body words, not script payloads."""
    body = re.sub(r"<script\b[^>]*>.*?</script>", " ", src, flags=re.S | re.I)
    words = re.findall(r"[A-Za-z]{2,}", re.sub(r"<[^>]+>", " ", body))
    fields = set()
    if len(words) >= 30:
        fields.update(("text", "markdown"))
    elif re.search(r"<(?:h[1-6]|table)\b", body, re.I):
        # A short page with real structure still has something to convert.
        # Scans the SCRIPT-STRIPPED body: an h-tag inside a JS string is
        # never visible content (spa_delay_page's only h-tag lived in a
        # script and falsely made markdown applicable).
        fields.add("markdown")
    if re.search(r"<title\b", src, re.I):
        fields.add("title")
    if re.search(r"<a\b[^>]*\bhref\s*=|<iframe\b[^>]*\b(?:src|data-src)\s*=",
                 src, re.I):
        # B59 folds <iframe> srcs into links/links_meta, so an embed page
        # without anchors still has applicable links.
        fields.update(("links", "links_meta"))
    if re.search(r"og:image|twitter:image|<img\b|srcset|poster\s*=", src, re.I):
        fields.update(("images", "image_variants"))
    if (re.search(r'<meta\b[^>]*\b(?:name|property|itemprop)\s*=', src, re.I)
            or re.search(r"<title\b", src, re.I)
            or "ld+json" in src.lower()):
        # charset and http-equiv declarations are not page metadata; a
        # <title> or JSON-LD payload is, because FIELD_RULES copies both.
        fields.add("metadata")
    # JSON-LD and microdata both land in the structured_data producer.
    if ("ld+json" in src.lower()
            or re.search(r"\bitemscope\b|\bitemprop\s*=", src, re.I)):
        fields.add("json_ld")
    fields.add("raw_html")
    if re.search(r"<table\b", src, re.I):
        fields.add("tables")
    # datetime covers <time datetime>; htmldate's cheap pass also reads a
    # bare <time> and <meta name="date">, so those count as applicable.
    if (re.search(r"datetime|datePublished|article:published_time|<time\b",
                  src, re.I)
            or re.search(r'<meta\b[^>]*\b(?:name|property)\s*=\s*["\']'
                         r'(?:date|publish(?:ed)?[_-]?date|date[_-]?published)["\']',
                         src, re.I)):
        fields.add("date")
    if re.search(r"<iframe\b", src, re.I):
        fields.add("iframes")
    if re.search(r'class\s*=\s*["\'][^"\']*(?:references?|footnotes?|citations?)',
                 src, re.I):
        fields.add("citations")
    return fields


def applicable_summary(results):
    """Applicable-cell recall and blocked rate for the headline.

    A cell counts when the fixture is not blocked and its source carries the
    field. Blocked fixtures are N/A: their cells leave the denominator, and
    the blocked-rate line reports them separately (challenger changes 1-2,
    miss-matrix F1)."""
    hits = total = blocked = 0
    for r in results:
        if r.get("completeness") == "blocked":
            blocked += 1
            continue
        applicable = source_applicable_fields(r["fixture"])
        total += len(applicable)
        hits += sum(1 for f in applicable if r.get("fields", {}).get(f))
    n = len(results)
    return {
        "hits": hits,
        "total": total,
        "recall_pct": round(100.0 * hits / total, 1) if total else 0.0,
        "blocked_n": blocked,
        "blocked_pct": round(100.0 * blocked / n, 1) if n else 0.0,
        "n": n,
    }


def coverage_summary(results):
    """Two coverage numbers: the original 13 (regression set) and the full
    richer batch. Each is the mean of the per-fixture field_coverage_pct,
    so the numbers line up with the per-fixture column."""
    reg = [r for r in results if r.get("fixture") in REGRESSION_FIXTURES]

    def _avg(rows):
        return round(sum(r["field_coverage_pct"] for r in rows) / len(rows), 1) if rows else 0.0

    return {
        "regression_n": len(reg),
        "regression_pct": _avg(reg),
        "full_n": len(results),
        "full_pct": _avg(results),
    }


def regression_passes(summary, full_suite=False):
    """Does this summary clear the floor(s)?

    `full_suite` adds the full-suite floor. It is only set when the run
    scored every fixture: a --fixture subset is held to the regression
    floor alone, because the suite average is not a property of one page.
    """
    if summary["regression_pct"] < REGRESSION_COVERAGE_FLOOR:
        return False
    return not full_suite or summary["full_pct"] >= FULL_COVERAGE_FLOOR


def markdown_summary(results):
    """Aggregate markdown presence and format integrity across results.

    Heading and table denominators count only what the sources actually
    carry; a fixture with no <table> contributes nothing to the table
    ratio, and pipe rows on table-less fixtures are ignored. tag_leaks is
    the sum of leaked tags and comments across every fixture.
    """
    source_h = sum(r["markdown_integrity"]["source_headings"] for r in results)
    roundtrip = sum(r["markdown_integrity"]["headings_roundtrip"]
                    for r in results)
    source_t = sum(r["markdown_integrity"]["source_tables"] for r in results)
    pipes = sum(r["markdown_integrity"]["pipe_tables"]
                for r in results if r["markdown_integrity"]["source_tables"])
    return {
        "present": sum(1 for r in results
                       if r.get("fields", {}).get("markdown")),
        "n": len(results),
        "headings_roundtrip": roundtrip,
        "source_headings": source_h,
        "tables_preserved": min(pipes, source_t),
        "source_tables": source_t,
        "tag_leaks": sum(r["markdown_integrity"]["tag_leaks"] for r in results),
    }


def full_result_row(url, result):
    """One index-ready record per fixture: the fields scripts/index_crawl.py
    loads. Straight from the ensemble result, never from the score dict."""
    return {
        "url": url,
        "title": result.get("title") or "",
        "text": result.get("text") or "",
        "json_ld": result.get("json_ld") or [],
        "metadata": result.get("metadata") or {},
    }


def write_full_results(output_dir, rows):
    """Best-effort atomic dump of full_result_row() records.

    tmp + os.replace in the same directory, so a reader never sees a partial
    file. Any failure prints a warning and returns: the dump is never on the
    eval's exit path.
    """
    path = os.path.join(output_dir, FULL_RESULTS_NAME)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        os.replace(tmp, path)
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"WARNING: full-results dump skipped ({e})", file=sys.stderr)


def start_fixture_server():
    """Serve FIXTURES_DIR on a free localhost port. Returns (url, httpd)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()

    def serve(self):
        path = self.path.lstrip("/")
        if not path:
            path = "index.html"
        full = os.path.join(FIXTURES_DIR, path)
        if os.path.isfile(full):
            with open(full, "rb") as f:
                data = f.read()
            if full.endswith(".html"):
                ct = "text/html; charset=utf-8"
            elif full.endswith(".pdf"):
                ct = "application/pdf"
            else:
                ct = "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            serve(self)

        def log_message(self, format, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/", httpd


def _rate_text(summary, suffix=""):
    """One funnel stage for stdout: '97.6% (23/25)' or 'n/a'."""
    if summary["pct"] is None:
        return "n/a"
    return f"{summary['pct']:.1f}% ({summary['hits']}/{summary['n']}{suffix})"


def main():
    parser = argparse.ArgumentParser(description="Run /crawl capability eval")
    parser.add_argument("--fixture", help="Run a single fixture by name")
    parser.add_argument("--output", default=RESULTS_DIR, help="Output directory for results")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args()

    # The crawler is private and not shipped here; the scorer above is
    # stdlib-only so recorded results can be re-scored without it.
    from scripts import ensemble

    os.makedirs(args.output, exist_ok=True)

    base_url, httpd = start_fixture_server()

    fixture_files = sorted(
        f for f in os.listdir(FIXTURES_DIR)
        if f.endswith((".html", ".pdf"))
    )
    if args.fixture:
        fixture_files = [f for f in fixture_files if args.fixture in f]

    results = []
    full_rows = []
    for fname in fixture_files:
        url = base_url + fname
        t0 = time.monotonic()
        try:
            result = ensemble.crawl(url)
        except Exception as e:
            result = {"error": str(e), "completeness": {"level": "blocked"}}
        elapsed = time.monotonic() - t0
        fixture_key = fname.replace(".html", "").replace(".pdf", "")
        score = score_result(fixture_key, result, elapsed)
        score["url"] = url
        results.append(score)
        full_rows.append(full_result_row(url, result))
        if not args.json:
            status = "OK" if score["completeness_match"] else "MISMATCH"
            print(f"  {fixture_key:30s} {score['completeness'] or '?':8s} "
                  f"fields={score['field_count']}/{len(SCORED_FIELDS)} "
                  f"cov={score['field_coverage_pct']}% "
                  f"elapsed={score['elapsed_s']}s {status}")
            for rec in score["sentinel_failed"]:
                kind = "absent" if rec["absent"] else "contains"
                print(f"    SENTINEL FAIL {fixture_key}: {rec['field']} "
                      f"must {kind} {rec['needle']!r}")

    httpd.shutdown()

    # Write results
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(args.output, f"eval_{ts}.jsonl")
    with open(out_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    # Index-ready dump beside the summary: atomic and best-effort, never on
    # the exit path (a broken dump target must not fail the eval).
    write_full_results(args.output, full_rows)

    # Summary
    total = len(results)
    matches = sum(1 for r in results if r["completeness_match"])
    avg_cov = sum(r["field_coverage_pct"] for r in results) / total if total else 0
    avg_elapsed = sum(r["elapsed_s"] for r in results) / total if total else 0
    sentinels_ok = all(r["sentinel_ok"] for r in results)
    cov = coverage_summary(results)
    floor_ok = regression_passes(cov, full_suite=not args.fixture)
    app = applicable_summary(results)
    print(f"\nEval complete: {total} fixtures, {matches}/{total} completeness match, "
          f"avg field coverage {avg_cov:.1f}%, avg elapsed {avg_elapsed:.2f}s")
    print(f"CAPABILITY = Applicable-cell recall: {app['recall_pct']:.1f}% "
          f"({app['hits']}/{app['total']}); source-applicable cells only, "
          "blocked fixtures = N/A")
    print(f"Coverage full-set (fixture-limited secondary, {cov['full_n']} fixtures): "
          f"{cov['full_pct']:.1f}% (floor {FULL_COVERAGE_FLOOR:.1f}%)")
    print(f"Coverage regression-set (original {cov['regression_n']} fixtures): "
          f"{cov['regression_pct']:.1f}% (floor {REGRESSION_COVERAGE_FLOOR:.1f}%)")
    md = markdown_summary(results)
    print(f"Markdown integrity: {md['present']}/{md['n']} present | "
          f"headings {md['headings_roundtrip']}/{md['source_headings']} round-trip | "
          f"tables {md['tables_preserved']}/{md['source_tables']} preserved | "
          f"tag leaks {md['tag_leaks']}")
    print(f"Blocked rate: {app['blocked_pct']:.1f}% "
          f"({app['blocked_n']}/{app['n']} fixtures)")
    if not floor_ok:
        print(f"FLOOR: regression-set {cov['regression_pct']:.1f}% "
              f"(floor {REGRESSION_COVERAGE_FLOOR:.1f}%), full-suite "
              f"{cov['full_pct']:.1f}% (floor {FULL_COVERAGE_FLOOR:.1f}%)")
    print(f"Sentinels: {sum(r['sentinel_passed'] for r in results)}/"
          f"{sum(r['sentinel_gated'] for r in results)} gated passed, "
          f"{sum(len(r['sentinel_pending']) for r in results)} pending, "
          f"{sum(len(r['sentinel_na']) for r in results)} N/A")
    # C5: the leaderboard metrics, over the same score rows.
    cm = report.completeness_summary(results)
    if cm["anchor_recall_pct"] is None:
        print("Completeness (anchor recall vs verbatim anchors): "
              "n/a (no verbatim anchors in this run)")
    else:
        print(f"Completeness (anchor recall vs verbatim anchors): "
              f"{cm['anchor_recall_pct']:.1f}% "
              f"({cm['anchors_passed']}/{cm['anchors_total']})")
    print(f"Fetch funnel: raw {_rate_text(cm['raw'])} -> "
          f"access {_rate_text(cm['access'])} -> "
          f"useful {_rate_text(cm['useful'], ' graded')}")
    if cm["useful"]["rate"] is None:
        print("Useful score, category-weighted (95% CI): "
              "n/a (no graded fixtures in this run)")
    else:
        print(f"Useful score, category-weighted (95% CI): "
              f"{cm['useful']['rate'] * 100:.1f}% "
              f"(±{cm['useful']['ci'] * 100:.1f} pp)")
    print(f"McNemar (access vs useful, {cm['mcnemar']['shared']} shared "
          f"graded): exact p = {cm['mcnemar']['p']:.3f} "
          f"({cm['mcnemar']['b']} vs {cm['mcnemar']['c']} discordant)")
    print(f"Results: {out_path}")

    if args.json:
        print(json.dumps(results, indent=2))

    # Exit code: 0 only if every completeness expectation holds, every gated
    # sentinel holds, and both floors hold (the full-suite floor only on a
    # full run).
    sys.exit(0 if matches == total and sentinels_ok and floor_ok else 1)


if __name__ == "__main__":
    main()
