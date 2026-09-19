#!/usr/bin/env python3
"""Normalize a CycloneDX SBOM into the same findings-shaped JSON that
template.html renders -- components stand in for findings, and component
type (operating-system / library / application) stands in for severity, so
the SBOM gets the same summary-card + searchable-list + detail-panel layout
as the SARIF-based tool reports instead of a one-off design.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", default="SBOM")
    ap.add_argument("--out", required=True)
    ap.add_argument("sbom_file")
    args = ap.parse_args()

    with open(args.sbom_file, encoding="utf-8") as f:
        doc = json.load(f)

    items = []
    summary = {}
    for c in doc.get("components", []):
        ctype = c.get("type", "unknown")
        summary[ctype] = summary.get(ctype, 0) + 1

        licenses = ", ".join(
            lic.get("license", {}).get("id") or lic.get("license", {}).get("name") or lic.get("expression", "")
            for lic in c.get("licenses", [])
        ) or "Unlicensed / not declared"
        supplier = c.get("supplier", {}).get("name", "")

        detail_lines = [f"Version: {c.get('version', '')}"]
        if supplier:
            detail_lines.append(f"Supplier: {supplier}")
        detail_lines.append(f"License(s): {licenses}")
        if c.get("purl"):
            detail_lines.append(f"PURL: {c.get('purl')}")

        items.append({
            "ruleId": c.get("bom-ref", ""),
            "title": c.get("name", "unknown"),
            "severity": ctype,
            "score": None,
            "message": f"v{c.get('version', '?')} - {licenses}",
            "remediation": "\n".join(detail_lines),
            "helpUri": "",
            "file": c.get("purl", ""),
            "line": None,
            "tags": [ctype],
            "source": os.path.basename(args.sbom_file),
        })

    output = {
        "tool": args.tool,
        "kind": "inventory",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "summary": summary,
        "findings": items,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f)

    print(f"{args.tool}: {len(items)} components -> {args.out} ({summary})", file=sys.stderr)


if __name__ == "__main__":
    main()
