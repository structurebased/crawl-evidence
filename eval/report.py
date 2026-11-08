#!/usr/bin/env python3
"""eval/report.py — generate a human-readable scorecard from eval results.

Produces a markdown report with per-fixture scores, field coverage matrix,
failure taxonomy, and trend summary.

Usage:
    python3 eval/report.py [--output FILE]
"""
import argparse
import json
import math
import os
import re
import urllib.parse
from collections import Counter, defaultdict

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(SKILL_DIR, "eval", "results")
# A6: per-request telemetry for the crawl-yield headline. The private repo
# gitignores this file; this evidence repo ships an 8,162-request snapshot
# at this path, and the report publishes n/a when it is absent.
RECORDS_FILE = os.path.join(SKILL_DIR, "learn", "benchmarks", "records.jsonl")

# Crawl-yield dedup key, vendored from the engine's frontier so this repo
# reproduces unique-page counts without shipping scripts/.
_TRACKING_PARAMS = {"fbclid", "gclid", "icid", "mc_cid", "mc_eid"}
_PCT_HEX = re.compile(r"%([0-9a-fA-F]{2})")


def _canonical_path(path):
    """RFC 3986 5.2.4 remove_dot_segments + percent-hex uppercase.

    Operates on the path only: '%2e' is never decoded into a dot, so an
    encoded traversal cannot change the key shape.
    """
    segs = []
    for seg in path.split("/"):
        if seg == ".":
            continue
        if seg == "..":
            if segs and segs[-1] not in ("", ".."):
                segs.pop()
            elif segs and segs[-1] == "..":
                segs.append(seg)
            continue
        segs.append(seg)
    out = "/".join(segs) or "/"
    return _PCT_HEX.sub(lambda m: "%" + m.group(1).upper(), out)


def canonical_url(url):
    """Stable dedup key: scheme+host(+port) lowercase, path folded,
    query sorted with tracking params dropped, fragment removed."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return url
    host = (p.hostname or "").lower()
    if not host:
        return url
    scheme = p.scheme.lower()
    try:
        port = p.port
    except Exception:
        port = None
    defaults = 80 if scheme == "http" else 443 if scheme == "https" else None
    if port == defaults:
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = _canonical_path(p.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    qs = []
    for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
        # utm_ is a prefix (utm_id, utm_source-platform, ...), not five names.
        kl = k.lower()
        if kl.startswith("utm_") or kl in _TRACKING_PARAMS:
            continue
        qs.append((kl, v))
    qs.sort()
    out = f"{scheme}://{netloc}{path}"
    q = urllib.parse.urlencode(qs)
    if q:
        out += "?" + q
    return out

# Field sets live here, not in eval/runner.py: the scorer's SCORED_FIELDS
# grows over time, and these two fixed denominators are what make a report
# row comparable to the same row from an older run.
OLD8_FIELDS = (
    "text", "title", "links", "images",
    "metadata", "json_ld", "raw_html", "tables",
)
FIELDS13 = OLD8_FIELDS + (
    "date", "iframes", "links_meta", "image_variants", "citations",
)


def coverage_pct(fields, field_set):
    """Presence over one named field set, so the denominator is explicit."""
    return 100.0 * sum(1 for f in field_set if fields.get(f)) / len(field_set)


# tests/test_contract_properties.py injects this backend; its rows are
# synthetic writes into the same telemetry file, not crawled pages.
SYNTHETIC_BACKEND = "stub_ok"


def load_records():
    """records.jsonl rows, or None when the file is absent/unreadable.

    Append-only telemetry can end mid-write after a crash, so torn lines
    are skipped rather than failing the scorecard.
    """
    rows = []
    try:
        with open(RECORDS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return None
    return rows


def crawl_yield(rows):
    """Unique useful pages per 1,000 requests, from records.jsonl rows.

    request = one row; useful = learn.py success (no error + non-empty
    text) with a non-empty URL and a real backend; unique = the canonical
    URL, i.e. the frontier's offer() dedup key, so tracking-param and
    trailing-slash aliases collapse to one page.
    """
    requests = len(rows)
    successful = sum(1 for r in rows if r.get("success"))
    candidates = [r for r in rows if r.get("success") and r.get("url")]
    synthetic = [r for r in candidates
                 if r.get("backend_used") == SYNTHETIC_BACKEND]
    useful = [r for r in candidates
              if r.get("backend_used") != SYNTHETIC_BACKEND]
    unique = {canonical_url(r["url"]) for r in useful}
    return {
        "requests": requests,
        "successful": successful,
        "synthetic_rows": len(synthetic),
        "useful_requests": len(useful),
        "unique_pages": len(unique),
        "yield_per_1000": round(1000.0 * len(unique) / requests, 1)
        if requests else 0.0,
    }


# ---------------------------------------------------------------------------
# C5: leaderboard completeness metrics, computed over the scorer's score
# rows. The anchor set is each fixture's gated verbatim strings; useful is
# judged only where that ground truth exists.
# ---------------------------------------------------------------------------

USEFUL_RECALL_MIN = 0.3   # Nimble: useful means completeness >= 0.3


def anchor_recall(row):
    """Verbatim anchor recall for one fixture, None when nothing is gated."""
    total = row.get("sentinel_gated") or 0
    if not total:
        return None
    return (row.get("sentinel_passed") or 0) / total


def raw_ok(row):
    """Nimble raw fetch: the request completed without an error."""
    return not row.get("has_error")


def access_ok(row):
    """TRACE access: page data loaded, not a barrier or empty shell."""
    return raw_ok(row) and row.get("completeness") != "blocked"


def useful_ok(row):
    """Accessed and anchor recall >= 0.3; None on ungraded fixtures."""
    recall = anchor_recall(row)
    if recall is None:
        return None
    return access_ok(row) and recall >= USEFUL_RECALL_MIN


def category_weighted(rows, outcome):
    """TRACE score: mean of per-category rates with a binomial 95% CI.

    report.ts: rate = mean_c(hits_c / n_c), half-width =
    1.96 * sqrt(sum_c p_c(1-p_c) / n_c) / K. `pct` is the micro rate for
    the same rows, printed beside the weighted score.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[row.get("page_type") or "unknown"].append(row)
    categories = []
    for category in sorted(groups):
        members = groups[category]
        hits = sum(1 for row in members if outcome(row))
        categories.append({"category": category, "n": len(members),
                           "hits": hits, "rate": hits / len(members)})
    n = sum(c["n"] for c in categories)
    hits = sum(c["hits"] for c in categories)
    if not categories:
        return {"n": 0, "hits": 0, "pct": None, "rate": None, "ci": None,
                "categories": []}
    k = len(categories)
    variance = sum(c["rate"] * (1 - c["rate"]) / c["n"] for c in categories)
    return {
        "n": n,
        "hits": hits,
        "pct": round(100.0 * hits / n, 1) if n else None,
        "rate": sum(c["rate"] for c in categories) / k,
        "ci": 1.96 * math.sqrt(variance) / k,
        "categories": categories,
    }


def mcnemar_p(b, c):
    """Two-sided exact McNemar p-value from TRACE analysis.ts.

    n = b + c discordant pairs. For n > 2000 the continuity-corrected
    normal approximation TRACE uses, otherwise the exact binomial double
    tail 2 * sum_{i<=min(b,c)} C(n, i) / 2**n.
    """
    b, c = int(b), int(c)
    n = b + c
    if n <= 0:
        return 1.0
    if n > 2000:
        z = (abs(b - c) - 1) / math.sqrt(n)
        p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2))))
        return min(1.0, max(0.0, p))
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def completeness_summary(rows):
    """Nimble funnel plus TRACE stats over the scorer's score rows.

    Raw and access run over every fixture. Useful runs over graded
    fixtures (gated verbatim anchors) only; the others are counted in
    `ungraded_n`. The paired test compares access against useful on the
    shared graded fixtures, where both judgments are defined.
    """
    graded = [row for row in rows if (row.get("sentinel_gated") or 0) > 0]
    anchors_total = sum(row.get("sentinel_gated") or 0 for row in rows)
    anchors_passed = sum(row.get("sentinel_passed") or 0 for row in rows)
    b = sum(1 for row in graded
            if access_ok(row) and not useful_ok(row))
    c = sum(1 for row in graded
            if useful_ok(row) and not access_ok(row))
    return {
        "anchors_total": anchors_total,
        "anchors_passed": anchors_passed,
        "anchor_recall_pct": round(100.0 * anchors_passed / anchors_total, 1)
        if anchors_total else None,
        "raw": category_weighted(rows, raw_ok),
        "access": category_weighted(rows, access_ok),
        "useful": category_weighted(graded, useful_ok),
        "graded_n": len(graded),
        "ungraded_n": len(rows) - len(graded),
        "mcnemar": {"b": b, "c": c, "p": mcnemar_p(b, c),
                    "shared": len(graded)},
    }


def load_latest():
    files = sorted(
        f for f in os.listdir(RESULTS_DIR)
        if f.startswith("eval_") and f.endswith(".jsonl") and "_classified" not in f
    )
    if not files:
        return None, None
    with open(os.path.join(RESULTS_DIR, files[-1])) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return files[-1], rows


def load_classified():
    files = sorted(
        f for f in os.listdir(RESULTS_DIR)
        if f.endswith("_classified.jsonl")
    )
    if not files:
        return None
    with open(os.path.join(RESULTS_DIR, files[-1])) as f:
        return [json.loads(line) for line in f if line.strip()]


def generate_report():
    fname, rows = load_latest()
    if not rows:
        return "No eval results found. Run `make eval` first."

    classified = load_classified()
    tag_counts = Counter()
    if classified:
        for r in classified:
            for t in r.get("failure_tags", []):
                tag_counts[t] += 1

    lines = []
    lines.append("# /crawl Capability Eval Scorecard")
    lines.append("")
    lines.append(f"**Run:** {fname}")
    lines.append(f"**Fixtures:** {len(rows)}")
    lines.append("")

    matches = sum(1 for r in rows if r.get("completeness_match"))
    avg_cov13 = sum(coverage_pct(r.get("fields", {}), FIELDS13) for r in rows) / len(rows)
    avg_old8 = sum(coverage_pct(r.get("fields", {}), OLD8_FIELDS) for r in rows) / len(rows)
    avg_elapsed = sum(r.get("elapsed_s", 0) for r in rows) / len(rows)
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Completeness match | {matches}/{len(rows)} |")
    lines.append(f"| Avg field coverage (13 fields) | {avg_cov13:.1f}% |")
    lines.append(f"| Avg old-8 coverage (8 fields) | {avg_old8:.1f}% |")
    lines.append(f"| Avg elapsed | {avg_elapsed:.2f}s |")
    # A6 headline: pages that survived the request budget, normalized so a
    # 100-URL run and an 8,000-request log are comparable.
    records = load_records()
    yld = crawl_yield(records) if records else None
    if yld and yld["requests"]:
        lines.append("| Crawl yield (unique useful pages / 1,000 requests) "
                     f"| {yld['yield_per_1000']} |")
    else:
        lines.append("| Crawl yield (unique useful pages / 1,000 requests) "
                     "| n/a (no records.jsonl) |")
    # D4: the leaderboard rows. Access and useful are separate judgments,
    # so one blended success rate cannot hide a blocked-body regression.
    cm = completeness_summary(rows)
    if cm["anchor_recall_pct"] is None:
        lines.append("| Verbatim anchor recall | n/a (no gated anchors) |")
    else:
        lines.append(f"| Verbatim anchor recall | {cm['anchor_recall_pct']:.1f}% "
                     f"({cm['anchors_passed']}/{cm['anchors_total']} strings) |")
    lines.append(f"| Access success (category-weighted) | "
                 f"{cm['access']['rate'] * 100:.1f}% "
                 f"±{cm['access']['ci'] * 100:.1f} pp (95% CI; "
                 f"{cm['access']['hits']}/{cm['access']['n']} fixtures across "
                 f"{len(cm['access']['categories'])} types) |")
    if cm["useful"]["rate"] is None:
        lines.append("| Useful success (category-weighted) | "
                     "n/a (no graded fixtures) |")
    else:
        lines.append(f"| Useful success (category-weighted) | "
                     f"{cm['useful']['rate'] * 100:.1f}% "
                     f"±{cm['useful']['ci'] * 100:.1f} pp (95% CI; "
                     f"{cm['useful']['hits']}/{cm['useful']['n']} graded; "
                     f"{cm['ungraded_n']} ungraded) |")
    stages = []
    for key in ("raw", "access", "useful"):
        s = cm[key]
        stages.append("n/a" if s["pct"] is None
                      else f"{s['pct']:.1f}% ({s['hits']}/{s['n']})")
    lines.append(f"| Fetch funnel (raw -> access -> useful) | "
                 f"{' -> '.join(stages)} |")
    lines.append(f"| Access vs useful, paired (exact McNemar) | p = "
                 f"{cm['mcnemar']['p']:.3f} ({cm['mcnemar']['b']} vs "
                 f"{cm['mcnemar']['c']} discordant on "
                 f"{cm['mcnemar']['shared']} shared graded) |")
    lines.append("")
    if yld and yld["requests"]:
        lines.append(
            f"Crawl-yield basis: {yld['requests']} requests, "
            f"{yld['successful']} raw successes, "
            f"{yld['synthetic_rows']} synthetic {SYNTHETIC_BACKEND} rows "
            f"excluded, {yld['useful_requests']} useful requests, "
            f"{yld['unique_pages']} unique canonical URLs "
            "(crawl_frontier.offer key). records.jsonl carries no body "
            "text, so the mark_fetched content-hash and simhash-band "
            "collapse are not applied; add them when a frontier journal "
            "with text_hash is the source.")
        lines.append("")

    lines.append("## Tool coverage")
    lines.append("")
    lines.append("| Tool | Coverage (13 fields) | Old-8 coverage (8 fields) |")
    lines.append("|---|---:|---:|")
    lines.append(f"| /crawl | {avg_cov13:.1f}% | {avg_old8:.1f}% |")
    lines.append("")

    lines.append("## Per-fixture scores")
    lines.append("")
    lines.append("| Fixture | Type | Completeness | Match | Fields (of 13) | "
                 "Coverage (13 fields) | Old-8 coverage (8 fields) | Elapsed | Source |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: x["fixture"]):
        match = "Y" if r.get("completeness_match") else "N"
        fields = r.get("fields", {})
        n13 = sum(1 for f in FIELDS13 if fields.get(f))
        lines.append(
            f"| {r['fixture']} | {r.get('page_type', '?')} | {r.get('completeness', '?')} | "
            f"{match} | {n13}/13 | "
            f"{coverage_pct(fields, FIELDS13):.1f}% | "
            f"{coverage_pct(fields, OLD8_FIELDS):.1f}% | "
            f"{r.get('elapsed_s', 0)}s | {r.get('text_source', '?')} |"
        )
    lines.append("")

    lines.append("## Field coverage matrix")
    lines.append("")
    fields = list(rows[0].get("fields", {}).keys()) if rows else []
    header = "| Fixture | " + " | ".join(fields) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(fields) + 1))
    for r in sorted(rows, key=lambda x: x["fixture"]):
        vals = " | ".join("Y" if r.get("fields", {}).get(f) else "N" for f in fields)
        lines.append(f"| {r['fixture']} | {vals} |")
    lines.append("")

    if tag_counts:
        lines.append("## Failure taxonomy")
        lines.append("")
        lines.append("| Tag | Count |")
        lines.append("|---|---|")
        for tag, count in tag_counts.most_common():
            lines.append(f"| {tag} | {count} |")
        lines.append("")

    if classified:
        lines.append("## Per-fixture failure tags")
        lines.append("")
        for r in sorted(classified, key=lambda x: x["fixture"]):
            tags = r.get("failure_tags", [])
            if tags:
                lines.append(f"- **{r['fixture']}**: {', '.join(tags)}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate eval scorecard")
    parser.add_argument("--output", help="Write report to file")
    args = parser.parse_args()

    report = generate_report()
    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
