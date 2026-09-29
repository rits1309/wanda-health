#!/usr/bin/env python3
"""Emit GitHub Actions annotations (::error::/::notice::) for one tool's
findings, from the same normalized findings JSON normalize_sarif.py
already produced for this pipeline's own internal report -- so a
reviewer sees this specific tool (and service, where relevant)'s own
pass/fail status, plus a real inline annotation on the PR diff (file +
line) for each finding at a severity this tool actually blocks on.

Deliberately not GitHub's native Code Scanning SARIF upload -- see
reusable-secops-scan.yml's CodeQL step (`upload: never`) for why: this
pipeline keeps every tool's findings in ONE consistent internal report
system (build_index.py's dashboard, the PR comment) instead of splitting
some tools into GitHub's separate Code Scanning UI and others not.
Workflow commands need no such upload and no security-events permission.

Usage:
  emit_annotations.py --tool "Semgrep" --data semgrep-findings.json \
    --blocking-severities critical,high [--service booking]
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--service", default=None)
    # Comma-separated, matching build_index.py's TOOL_BLOCKING_SEVERITIES
    # for this same tool -- kept as an explicit argument rather than
    # hardcoded here so this one script serves every tool (including
    # Checkov's own critical-only rule in wanda-altcloud-infra-code's
    # copy of this pipeline) without a per-tool special case in this file.
    ap.add_argument(
        "--blocking-severities", default="",
        help="Comma-separated severities this tool blocks on, e.g. 'critical,high'. "
             "Empty means this tool never blocks -- a PASS notice is still emitted.",
    )
    args = ap.parse_args()

    blocking = {s.strip().lower() for s in args.blocking_severities.split(",") if s.strip()}
    label = args.tool + (f" ({args.service})" if args.service else "")

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)

    findings = data.get("findings", [])
    breaching = [f for f in findings if (f.get("severity") or "").lower() in blocking]

    # One annotation per breaching finding, with file/line when known --
    # GitHub renders that as a real inline comment on the PR's "Files
    # changed" tab, not just a line in the job log.
    for f in breaching:
        sev = (f.get("severity") or "").upper()
        rule = f.get("ruleId") or ""
        message = (f.get("message") or f.get("title") or "").replace("\n", " ").strip()
        loc_file = f.get("file") or ""
        loc_line = f.get("line")
        where = f"file={loc_file},line={loc_line}" if loc_file and loc_line else ""
        prefix = f"::error {where}" if where else "::error"
        print(f"{prefix}::{label} [{sev}] {rule}: {message}")

    if breaching:
        sev_list = "/".join(s.upper() for s in sorted(blocking))
        print(f"::error::{label}: FAIL -- {len(breaching)} finding(s) at {sev_list}")
    else:
        print(f"::notice::{label}: PASS -- no findings at a blocking severity")


if __name__ == "__main__":
    main()
