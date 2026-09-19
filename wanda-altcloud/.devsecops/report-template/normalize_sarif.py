#!/usr/bin/env python3
"""Normalize one or more SARIF files into the common findings JSON consumed
by report-template/template.html.

Handles both rule-reference shapes seen across our tools:
  - Semgrep / Trivy / Grype: result.ruleIndex into tool.driver.rules[]
  - CodeQL: result.rule.index + result.rule.toolComponent.index into
    tool.extensions[componentIndex].rules[] (query packs are loaded as
    separate SARIF "extensions", not the driver itself)
and both severity sources seen across our tools:
  - CodeQL / Trivy / Grype: rule.properties["security-severity"], a CVSS-like
    0-10 score -- bucketed using the standard CVSS qualitative scale.
  - Semgrep: no security-severity anywhere in its SARIF (confirmed against a
    real scan covering 1000+ rules) -- falls back to SARIF level
    (error/warning/note).

Usage: normalize_sarif.py --tool "Semgrep" --out out.json file1.sarif [file2.sarif ...]
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

_SEV_WORDS = {"critical", "high", "medium", "low"}


def severity_from_words(rule):
    """Prefer the tool's own stated severity word over a derived CVSS bucket
    -- Trivy tags its rules with a plain CRITICAL/HIGH/MEDIUM/LOW tag, and
    Grype's shortDescription always reads "<word> vulnerability for ...".
    Both were confirmed against real scan output to disagree with a
    CVSS-score bucketing of their own security-severity field often enough
    (a Grype finding scored 8.0 -- CVSS "high" -- while Grype itself calls
    it "medium", since GHSA and NVD scoring don't always agree) that the
    tool's own word is the more trustworthy signal when it's available.
    """
    for tag in rule.get("properties", {}).get("tags", []):
        if isinstance(tag, str) and tag.lower() in _SEV_WORDS:
            return tag.lower()
    short_desc = rule.get("shortDescription", {}).get("text", "")
    m = re.search(r"\b(critical|high|medium|low)\b\s+vulnerability", short_desc, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return None


def severity_from_score(score):
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0.0:
        return "low"
    return "info"


def parse_security_score(value):
    """Return a finite security score, or None when SARIF has no usable score."""
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) else None


def severity_from_level(level):
    return {"error": "high", "warning": "medium", "note": "low"}.get(level, "info")


def resolve_rule(run, result):
    """Return the rule dict for a result, or {} if it can't be resolved."""
    rule_ref = result.get("rule")
    if isinstance(rule_ref, dict) and "index" in rule_ref:
        comp_index = rule_ref.get("toolComponent", {}).get("index")
        extensions = run.get("tool", {}).get("extensions", [])
        if comp_index is not None and 0 <= comp_index < len(extensions):
            rules = extensions[comp_index].get("rules", [])
        else:
            rules = run.get("tool", {}).get("driver", {}).get("rules", [])
        idx = rule_ref["index"]
        if 0 <= idx < len(rules):
            return rules[idx]

    rule_index = result.get("ruleIndex")
    driver_rules = run.get("tool", {}).get("driver", {}).get("rules", [])
    if rule_index is not None and 0 <= rule_index < len(driver_rules):
        return driver_rules[rule_index]

    rule_id = result.get("ruleId")
    for r in driver_rules:
        if r.get("id") == rule_id:
            return r
    for ext in run.get("tool", {}).get("extensions", []):
        for r in ext.get("rules", []):
            if r.get("id") == rule_id:
                return r
    return {}


def extract_findings(sarif_path):
    with open(sarif_path, encoding="utf-8") as f:
        doc = json.load(f)

    findings = []
    for run in doc.get("runs", []):
        for result in run.get("results", []):
            rule = resolve_rule(run, result)
            props = rule.get("properties", {})

            sec_sev = parse_security_score(props.get("security-severity"))
            severity = severity_from_words(rule)
            if severity is None:
                if sec_sev is not None:
                    severity = severity_from_score(sec_sev)
                if severity is None:
                    severity = severity_from_level(result.get("level") or rule.get("defaultConfiguration", {}).get("level"))

            title = rule.get("shortDescription", {}).get("text") or result.get("ruleId") or "Untitled finding"
            message = result.get("message", {}).get("text", "")
            remediation = rule.get("help", {}).get("text") or rule.get("fullDescription", {}).get("text") or ""
            help_uri = rule.get("helpUri", "")

            loc_file, loc_line = "", None
            locations = result.get("locations") or []
            if locations:
                phys = locations[0].get("physicalLocation", {})
                loc_file = phys.get("artifactLocation", {}).get("uri", "")
                loc_line = phys.get("region", {}).get("startLine")

            tags = [t for t in props.get("tags", []) if isinstance(t, str)]

            findings.append({
                "ruleId": result.get("ruleId") or rule.get("id") or "",
                "title": title,
                "severity": severity,
                "score": sec_sev,
                "message": message,
                "remediation": remediation.strip(),
                "helpUri": help_uri,
                "file": loc_file,
                "line": loc_line,
                "tags": tags,
                "source": os.path.basename(sarif_path),
            })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("sarif_files", nargs="+")
    args = ap.parse_args()

    all_findings = []
    for path in args.sarif_files:
        all_findings.extend(extract_findings(path))

    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in all_findings:
        summary[f["severity"]] = summary.get(f["severity"], 0) + 1

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    all_findings.sort(key=lambda f: order.get(f["severity"], 5))

    output = {
        "tool": args.tool,
        "kind": "findings",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "summary": summary,
        "findings": all_findings,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f)

    print(f"{args.tool}: {len(all_findings)} findings -> {args.out} ({summary})", file=sys.stderr)


if __name__ == "__main__":
    main()
