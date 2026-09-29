#!/usr/bin/env python3
"""Fill report-template/template.html with one findings JSON file (produced
by normalize_sarif.py or normalize_sbom.py), a page title, and that tool's
official logo (see tool_icons.py).

Usage: render_report.py --tool Semgrep --title "Semgrep Report" --data semgrep-findings.json --out semgrep-report.html
"""
import argparse
import os

from tool_icons import TOOL_ICONS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True, help="Exact tool name, e.g. 'Semgrep' -- looked up in tool_icons.TOOL_ICONS")
    ap.add_argument("--title", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    with open(args.data, encoding="utf-8") as f:
        findings_json = f.read()
    # Belt-and-braces: a literal "</script>" inside the JSON payload (it
    # can't occur in well-formed JSON values here, but nothing in the SARIF
    # schema forbids arbitrary text in a message) would otherwise close the
    # embedding <script> tag early.
    findings_json = findings_json.replace("</script>", "<\\/script>")

    tool_icon = TOOL_ICONS.get(args.tool, "")

    html = (
        template
        .replace("__TITLE__", args.title)
        .replace("__TOOL_ICON__", tool_icon)
        .replace("__FINDINGS_JSON__", findings_json)
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Rendered {args.out}")


if __name__ == "__main__":
    main()
