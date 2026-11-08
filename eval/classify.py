#!/usr/bin/env python3
"""eval/classify.py — classify eval failures by root cause.

Tags are always drawn from docs/CONTRACT.md's 19-value failure_class
taxonomy; eval-side signals fold into the nearest value:
  network               -> dns | connection_error
  challenge             -> captcha | cloudflare
  parse                 -> unknown
  completeness_mismatch -> unknown
  missing_{field}       -> unknown
  blocked_unexpected    -> blocked_interstitial
  empty_text            -> empty_content
  none (clean row)      -> no tags
timeout and unknown were already contract values. Full rationale:
eval/research/exec-lane16-taxonomy.md.
"""
import argparse
import json
from collections import Counter


def classify_failure(row):
    """Classify a single eval result row.

    Returns failure_class values from docs/CONTRACT.md's taxonomy; a clean
    row returns an empty list (no failure to tag).
    """
    tags = []
    if not row.get("completeness_match", True):
        # eval expectation gap: the contract has no class for it
        tags.append("unknown")
    err = row.get("error") or ""
    err_lower = str(err).lower()
    if err:
        if any(k in err_lower for k in ("timeout", "timed out", "deadline")):
            tags.append("timeout")
        elif any(k in err_lower for k in ("dns", "name or service not known",
                                           "nodename nor servname")):
            tags.append("dns")
        elif any(k in err_lower for k in ("connection", "refused", "network",
                                           "unreachable")):
            tags.append("connection_error")
        elif "captcha" in err_lower:
            tags.append("captcha")
        elif any(k in err_lower for k in ("challenge", "cloudflare",
                                           "just a moment")):
            tags.append("cloudflare")
        else:
            # parse/decode/encoding and unrecognised errors: no contract
            # class for them, catch-all applies
            tags.append("unknown")
    fields = row.get("fields", {}) or {}
    if any(not present for present in fields.values()):
        # optional-field gaps are not crawl failures: catch-all applies
        tags.append("unknown")
    if row.get("completeness") == "blocked" and row.get("expected_completeness") == "complete":
        if not any(t in tags for t in ("cloudflare", "captcha")):
            tags.append("blocked_interstitial")
    if row.get("text_len", 0) == 0 and row.get("expected_completeness") == "complete":
        tags.append("empty_content")
    # several sources fold to "unknown"; keep one copy, order preserved
    return list(dict.fromkeys(tags))


def main():
    parser = argparse.ArgumentParser(description="Classify eval failures")
    parser.add_argument("results_file", help="Path to eval results JSONL")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--summary", action="store_true", help="Print summary only")
    args = parser.parse_args()

    with open(args.results_file) as f:
        rows = [json.loads(line) for line in f if line.strip()]

    classified = []
    for row in rows:
        tags = classify_failure(row)
        classified.append({"fixture": row["fixture"], "tags": tags})

    tag_counts = Counter()
    for c in classified:
        for t in c["tags"]:
            tag_counts[t] += 1

    if args.json:
        print(json.dumps({"classified": classified, "tag_counts": dict(tag_counts)}, indent=2))
    elif args.summary:
        print("Failure taxonomy:")
        for tag, count in tag_counts.most_common():
            print(f"  {tag}: {count}")
    else:
        for c in classified:
            print(f"{c['fixture']}: {', '.join(c['tags'])}")
        print()
        print("Summary:")
        for tag, count in tag_counts.most_common():
            print(f"  {tag}: {count}")

    out_path = args.results_file.replace(".jsonl", "_classified.jsonl")
    with open(out_path, "w") as f:
        for row, c in zip(rows, classified):
            row["failure_tags"] = c["tags"]
            f.write(json.dumps(row) + "\n")
    print(f"\nClassified results: {out_path}")


if __name__ == "__main__":
    main()