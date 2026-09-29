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


def severity_from_level(level, tool=None):
    """Fallback used only when a finding has no severity word (Trivy/Grype
    tags) and no security-severity score (CodeQL/Trivy/Grype) -- i.e. the
    tool gave us nothing but SARIF's own error/warning/note. That's a weak
    signal in general, and actively misleading for Checkov specifically:
    without a Bridgecrew/Prisma Cloud API key configured (see
    security_scan.yaml), Checkov's SARIF writer sets level="error" for
    EVERY failed check regardless of real severity, so the plain mapping
    below would report a flat "high" across the board -- confirmed against
    a real report showing 52/52 findings as "high", spanning checks that
    are obviously not uniformly high risk (e.g. "S3 bucket missing a
    lifecycle policy" next to "RDS missing IAM auth"). Capped at "low"
    for Checkov so this fallback doesn't claim more confidence than the
    data supports. Tools that DO carry real severity signal (Trivy's
    security-severity score, Trivy/Grype's severity tags) are resolved by
    severity_from_score/severity_from_words before this function is ever
    reached, so they're unaffected by this cap.
    """
    severity = {"error": "high", "warning": "medium", "note": "low"}.get(level, "info")
    if tool and tool.strip().lower() == "checkov" and severity == "high":
        severity = "low"
    return severity


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


def extract_findings(sarif_path, tool=None):
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
                    severity = severity_from_level(
                        result.get("level") or rule.get("defaultConfiguration", {}).get("level"),
                        tool=tool,
                    )

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
        all_findings.extend(extract_findings(path, tool=args.tool))

    # Collapse byte-identical results before anything counts them. Trivy's
    # config scan walks the same Terraform twice -- once as source
    # ("Type: terraform") and once as a plan snapshot
    # ("Type: terraformplan-snapshot", expanded per module instantiation) --
    # so one misconfiguration in one shared module arrives once per resource
    # that instantiates it. Left alone, a single public-subnet finding in
    # modules/networking/subnets/main.tf was counted four times and pushed
    # the HIGH total past its threshold on its own, failing the gate on a
    # number no reviewer could reconcile with the report they were reading.
    #
    # The key is every field that could tell two findings apart, message
    # included (Trivy writes the artifact and scan type into the message, so
    # source and plan-snapshot hits stay distinct). That makes this drop only
    # rows carrying no information the kept row doesn't already have: two
    # CVEs, two rules, or one rule at two locations all differ somewhere in
    # the key and survive. It therefore cannot merge genuinely distinct
    # results in the application repos, where one file/line legitimately
    # carries many package findings.
    seen = set()
    deduped = []
    for f in all_findings:
        key = (f["ruleId"], f["severity"], f["file"], f["line"], f["message"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    duplicates_dropped = len(all_findings) - len(deduped)
    all_findings = deduped

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

    dupe_note = f", {duplicates_dropped} duplicate(s) collapsed" if duplicates_dropped else ""
    print(
        f"{args.tool}: {len(all_findings)} findings -> {args.out} ({summary}){dupe_note}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()