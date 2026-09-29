#!/usr/bin/env python3
"""Build the CI run's dashboard front page (reports/index.html) over every
per-tool HTML report this run produced.

Reads each report's own embedded findings JSON -- the same
<script id="findings-data"> blob template.html renders from -- rather than
re-deriving severities independently, so the dashboard's counts can never
drift from what a reviewer sees after clicking through into a report.

Usage:
  build_index.py --reports-dir reports --run-id X --ref-name Y --sha Z \
    --out reports/index.html
"""
import argparse
import html
import json
import pathlib
import re

from tool_icons import TOOL_ICONS

FINDINGS_RE = re.compile(
    r'<script id="findings-data" type="application/json">(.*?)</script>', re.S
)

# Every tool gates on its OWN findings, checked independently against its
# OWN threshold(s) -- not pooled into one number summed across every tool.
# This is what lets different tools block on different severities, per the
# sign-off's own wording. Gitleaks has no severity concept of its own -- any
# result at all is what fails its job, independent of thresholds entirely
# (see security_scan.yaml's "Fail if leaks found" step). A tool not listed
# here (an inventory kind, or an unrecognized tool name) never blocks,
# regardless of what it reports.
#
# Medium/low never block on their own, from any tool, at any count -- per
# the sign-off: "everything else surfaces as a PR comment, not a hard
# block, otherwise you'll get gate fatigue in month one."
#
# Listed as a superset across the platform's repositories so this script
# stays copy-identical in behaviour wherever it runs: this repo produces
# Checkov and Trivy (config), the application repos produce the rest, and a
# tool that never reports simply never appears in a run's cards.
TOOL_BLOCKING_SEVERITIES = {
    "CodeQL": ("critical", "high"),
    "Semgrep": ("critical", "high"),
    "Trivy (filesystem)": ("critical", "high"),
    "Trivy (image)": ("critical", "high"),
    "Trivy (config)": ("critical", "high"),
    "Grype": ("critical", "high"),
    "Checkov": ("critical", "high"),
}

SEV_ORDER = ["critical", "high", "medium", "low", "info"]
SEV_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Info"}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def parse_report(html_path):
    text = html_path.read_text(encoding="utf-8", errors="replace")
    m = FINDINGS_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def slug_parts(folder_name):
    """"<tool-slug>-html-report[-<service>]" -> (tool_slug, service|None).
    "-html-report" is a literal marker every HTML-report upload step in this
    pipeline uses, so this is exact, not a guess."""
    m = re.match(r"^(.+?)-html-report(?:-(.+))?$", folder_name)
    if not m:
        return folder_name, None
    return m.group(1), m.group(2)


def build_card(folder_name, data, service, thresholds):
    is_inventory = data.get("kind") == "inventory"
    tool = data.get("tool", "Unknown")
    summary = data.get("summary", {})
    findings = data.get("findings", [])

    if is_inventory:
        return {
            "folder": folder_name, "tool": tool, "service": service, "is_inventory": True,
            "count": len(findings), "severity_counts": {}, "worst": None,
            "blocking": False, "is_blocking_tool": False, "breached_severities": [],
        }

    severity_counts = {k: summary.get(k, 0) for k in SEV_ORDER}
    worst = next((k for k in SEV_ORDER if severity_counts.get(k)), None)

    # Gitleaks has no severity concept of its own -- any result at all is
    # what fails its job (see security_scan.yaml's "Fail if leaks found"
    # step), independent of thresholds and of whichever bucket
    # normalize_sarif.py's SARIF-level fallback happened to assign it.
    if tool == "Gitleaks":
        is_blocking_tool = True
        breached_severities = []
        blocking = len(findings) > 0
    else:
        blocking_severities = TOOL_BLOCKING_SEVERITIES.get(tool, ())
        is_blocking_tool = bool(blocking_severities)
        # Checked per severity THIS tool actually cares about, against
        # THIS card's own count -- never pooled with any other tool or
        # service. A threshold raised for testing (SECURITY_THRESHOLD_*)
        # therefore tolerates that many findings PER tool+service, not a
        # shared allowance split across all of them.
        breached_severities = [
            s for s in blocking_severities
            if severity_counts.get(s, 0) > thresholds.get(s, 0)
        ]
        blocking = bool(breached_severities)

    return {
        "folder": folder_name, "tool": tool, "service": service, "is_inventory": False,
        "count": len(findings), "severity_counts": severity_counts, "worst": worst,
        "blocking": blocking, "is_blocking_tool": is_blocking_tool,
        "breached_severities": breached_severities,
        # Kept only so --counts-output can name exactly which finding, in
        # which file/line, is behind each number below -- a developer
        # reading "HIGH: 3" in the gate output has no way to act on it
        # without this. Not used anywhere else (the HTML report tiles
        # render straight from each report's own page, not from here).
        "raw_findings": findings,
    }


def sev_var(sev):
    return {"critical": "crit", "high": "high", "medium": "med", "low": "low", "info": "info"}.get(sev, "info")


def risk_color(risk_level):
    return {
        "CRITICAL": "crit", "HIGH": "high", "MEDIUM": "med", "LOW": "low",
        "MINIMAL": "ok", "NONE": "ok",
    }.get(risk_level, "info")


def render_pill(sev, count=None):
    label = SEV_LABEL.get(sev, sev)
    suffix = f" {count}" if count is not None else ""
    return (f'<span class="pill" style="--pill-color:var(--{sev_var(sev)});'
            f'--pill-soft:var(--{sev_var(sev)}-soft)">{esc(label)}{suffix}</span>')


def render_tile(card):
    classes = ["tile"]
    if card["is_inventory"]:
        classes.append("tile-inventory")
    elif card["blocking"]:
        classes.append("tile-blocking")
    elif card["worst"] in ("critical", "high"):
        classes.append("tile-attention")

    href = card["href"]
    icon = TOOL_ICONS.get(card["tool"], "")
    title = esc(card["tool"]) + (f' &middot; <span class="tile-service">{esc(card["service"])}</span>' if card["service"] else "")

    if card["is_inventory"]:
        badge = f'<span class="pill" style="--pill-color:var(--accent);--pill-soft:var(--accent-soft)">{card["count"]} component(s)</span>'
        sub = "Software bill of materials"
    elif card["count"] == 0:
        badge = '<span class="pill" style="--pill-color:var(--ok);--pill-soft:var(--ok-soft)">Clean</span>'
        sub = "No findings"
    else:
        worst = card["worst"]
        badge = render_pill(worst, card["count"])
        if card["tool"] == "Gitleaks" and card["blocking"]:
            sub = "Leaked secret -- blocks this run outright"
        elif card["blocking"]:
            breached = "/".join(s.title() for s in card["breached_severities"])
            sub = f"{breached} over this tool's own limit -- blocks this run"
        else:
            sub = "Within this tool's own severity limit"

    blocker_flag = '<span class="blocker-flag">BLOCKER</span>' if card["blocking"] else ""

    return f"""
    <a class="{' '.join(classes)}" href="{esc(href)}" data-worst="{esc(card['worst'] or '')}" data-blocking="{'1' if card['blocking'] else '0'}">
      <div class="tile-bar"></div>
      <div class="tile-icon">{icon}</div>
      <div class="tile-body">
        <div class="tile-top"><span class="tile-title">{title}</span>{blocker_flag}</div>
        <div class="tile-badges">{badge}</div>
        <div class="tile-sub">{esc(sub)}</div>
      </div>
    </a>"""


def render_other(folder_name, href):
    label = esc(folder_name.replace("-", " ").title())
    return f'<a class="other-link" href="{esc(href)}">{label}</a>'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports-dir", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--ref-name", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--generated-at", required=True)
    ap.add_argument("--out", required=True)
    # Per-tool threshold, as a raw COUNT of findings at that exact
    # severity for a given tool+service -- "more than N is too many,"
    # each severity checked independently, not a percentage and not
    # cumulative with more severe levels, and never pooled across tools
    # or services (see TOOL_BLOCKING_SEVERITIES above). Zero tolerance
    # for critical/high by design: a single critical or high finding from
    # any one tool is one too many. Medium/low never gate at all -- see
    # TOOL_BLOCKING_SEVERITIES's own comment -- these two thresholds only
    # drive the report's "found N, allowed N" display. Configurable via
    # repo Variables (SECURITY_THRESHOLD_*) rather than hardcoded here.
    ap.add_argument("--critical-threshold", type=int, default=0)
    ap.add_argument("--high-threshold", type=int, default=0)
    ap.add_argument("--medium-threshold", type=int, default=10)
    ap.add_argument("--low-threshold", type=int, default=100)
    # Written after the report itself is fully built, so a separate later
    # step can gate on it without risking the report never getting
    # published -- see security_scan.yaml's "Enforce risk gate" step.
    ap.add_argument("--risk-output", default=None)
    # Optional, purely additive: a small JSON summary (per-severity found
    # count, configured limit, and breach flag) so the gate step can print
    # a real Severity/Found/Allowed/Status table instead of a bare
    # "Risk level is X" line. --risk-output above is untouched -- still
    # just the bare risk-level word -- so any consumer reading only that
    # file keeps working unchanged.
    ap.add_argument("--counts-output", default=None)
    args = ap.parse_args()

    thresholds = {
        "critical": args.critical_threshold,
        "high": args.high_threshold,
        "medium": args.medium_threshold,
        "low": args.low_threshold,
    }

    reports_dir = pathlib.Path(args.reports_dir)
    cards = []
    other_links = []

    for d in sorted(p for p in reports_dir.iterdir() if p.is_dir()):
        htmls = sorted(d.glob("*.html")) or sorted(d.rglob("index.html"))
        if not htmls:
            continue
        report_path = htmls[0]
        href = report_path.relative_to(reports_dir).as_posix()

        data = parse_report(report_path)
        if data is None:
            other_links.append(render_other(d.name, href))
            continue

        _, service = slug_parts(d.name)
        card = build_card(d.name, data, service, thresholds)
        card["href"] = href
        cards.append(card)

    def sort_key(c):
        if c["is_inventory"]:
            bucket = 3
        elif c["blocking"]:
            bucket = 0
        elif c["worst"] in ("critical", "high"):
            bucket = 1
        else:
            bucket = 2
        return (bucket, c["tool"], c["service"] or "")

    cards.sort(key=sort_key)

    # Aggregate severity totals across every findings-kind (non-inventory)
    # report -- this is the number a reviewer sees before clicking anything.
    totals = {k: 0 for k in SEV_ORDER}
    for c in cards:
        if c["is_inventory"]:
            continue
        for k in SEV_ORDER:
            totals[k] += c["severity_counts"].get(k, 0)

    blocking_cards = [c for c in cards if c["blocking"]]
    total_findings = sum(c["count"] for c in cards if not c["is_inventory"])

    # Gitleaks blocks instantly and independently of every other tool's
    # own gate -- a leaked secret is a live incident regardless of what
    # anything else found. Split out here since it needs its own message
    # below, distinct from a severity-threshold breach.
    gitleaks_blocking = [c for c in blocking_cards if c["tool"] == "Gitleaks"]
    other_blocking = [c for c in blocking_cards if c["tool"] != "Gitleaks"]

    # risk_level/risk_count are derived FROM each card's own per-tool
    # blocking determination (see build_card/TOOL_BLOCKING_SEVERITIES),
    # not a pooled sum across every tool -- the run is blocked the moment
    # ANY single tool+service breaches ITS OWN threshold. The severity
    # label shown is simply the worst breached severity among the cards
    # that are actually blocking; aggregate_breach (below) is the real
    # pass/fail decision and does not depend on this label.
    if total_findings == 0:
        risk_level, risk_count = "NONE", 0
    elif other_blocking:
        worst_breached = min(
            (s for c in other_blocking for s in c["breached_severities"]),
            key=SEV_ORDER.index,
        )
        risk_level = worst_breached.upper()
        risk_count = sum(c["severity_counts"].get(worst_breached, 0) for c in other_blocking)
    else:
        risk_level, risk_count = "MINIMAL", 0

    aggregate_breach = bool(other_blocking)

    if risk_level == "NONE":
        risk_note = "no findings"
    elif risk_level == "MINIMAL":
        risk_note = "within every tool's own severity limit"
    else:
        breaching_sev = risk_level.lower()
        breaching_tools = sorted({
            c["tool"] + (f" ({c['service']})" if c["service"] else "")
            for c in other_blocking if breaching_sev in c["breached_severities"]
        })
        risk_note = f"{risk_count} {breaching_sev} finding(s) from {', '.join(breaching_tools)}"

    if gitleaks_blocking:
        gate_class = "gate-blocked"
        leak_count = sum(c["count"] for c in gitleaks_blocking)
        gate_title = f"{leak_count} leaked secret(s) found"
        gate_sub = "Gitleaks findings block this run outright, independent of every other tool's own gate."
    elif aggregate_breach:
        gate_class = "gate-blocked"
        gate_title = f"Severity limit exceeded: {risk_note}"
        gate_sub = "This run cannot pass until every tool listed above is back within its own severity limit."
    elif total_findings > 0:
        gate_class = "gate-clear"
        gate_title = "Within every tool's own severity limit"
        gate_sub = f"{total_findings} finding(s) reported across {len(cards)} check(s) -- none push their own tool over its limit."
    else:
        gate_class = "gate-clear"
        gate_title = "Clean run"
        gate_sub = "No findings from any check in this run."

    stat_buttons = "".join(
        f'<button type="button" class="stat" data-filter-sev="{k}" style="--stat-color:var(--{sev_var(k)})">'
        f'<span class="stat-bar"></span><span><span class="stat-label">{SEV_LABEL[k]}</span><br>'
        f'<span class="stat-count">{totals[k]}</span></span></button>'
        for k in SEV_ORDER
    )

    tiles_html = "".join(render_tile(c) for c in cards)
    other_html = (
        f'<div class="other-section"><div class="detail-section-label">Other artifacts</div>'
        f'<div class="other-links">{"".join(other_links)}</div></div>'
        if other_links else ""
    )
    empty_html = '<div class="tile-empty">No HTML report artifacts were produced by this run.</div>' if not cards else ""

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strata.Cloud CI Report</title>
<style>
  :root {{
    --bg:#F6F7FA; --surface:#FFFFFF; --surface-2:#EEF0F5; --ink:#161A23; --ink-muted:#5B6270; --ink-faint:#8890A0;
    --border:#DFE2EA; --accent:#4C4FE0; --accent-soft:#EAEAFC;
    --crit:#C4362E; --crit-soft:#FBEAEA;
    --high:#B8560B; --high-soft:#FBF0E6;
    --med:#9A7B0A;  --med-soft:#FBF6E2;
    --low:#2F6FB0;  --low-soft:#EAF2FA;
    --info:#5B6270; --info-soft:#EEF0F5;
    --ok:#1E8E5A;   --ok-soft:#E7F5EC;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#0F121A; --surface:#161A25; --surface-2:#1D2230; --ink:#E8EAF1; --ink-muted:#9BA2B4; --ink-faint:#6C7386;
      --border:#2A3040; --accent:#8D90FF; --accent-soft:#22243D;
      --crit:#FF6E64; --crit-soft:#3A1913;
      --high:#FFB25C; --high-soft:#3A2712;
      --med:#E8C34D;  --med-soft:#332D10;
      --low:#7FB0E8;  --low-soft:#132436;
      --info:#9BA2B4; --info-soft:#1D2230;
      --ok:#4ADE94;   --ok-soft:#0F2A1D;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg:#0F121A; --surface:#161A25; --surface-2:#1D2230; --ink:#E8EAF1; --ink-muted:#9BA2B4; --ink-faint:#6C7386;
    --border:#2A3040; --accent:#8D90FF; --accent-soft:#22243D;
    --crit:#FF6E64; --crit-soft:#3A1913;
    --high:#FFB25C; --high-soft:#3A2712;
    --med:#E8C34D;  --med-soft:#332D10;
    --low:#7FB0E8;  --low-soft:#132436;
    --info:#9BA2B4; --info-soft:#1D2230;
    --ok:#4ADE94;   --ok-soft:#0F2A1D;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: "IBM Plex Sans", -apple-system, "Segoe UI", sans-serif; line-height: 1.5;
  }}
  .mono {{ font-family: "IBM Plex Mono", Consolas, monospace; }}
  a {{ color: inherit; text-decoration: none; }}
  .wrap {{ max-width: 1320px; margin: 0 auto; padding: 28px 24px 48px; }}

  header {{ margin-bottom: 20px; }}
  .eyebrow {{
    font-family: "IBM Plex Mono", monospace; font-size: 11.5px; letter-spacing: .07em; text-transform: uppercase;
    color: var(--ink-faint); margin-bottom: 6px;
  }}
  h1 {{ font-size: 1.6rem; margin: 0 0 6px; text-wrap: balance; }}
  .meta {{ font-size: 13px; color: var(--ink-muted); }}
  .meta b {{ color: var(--ink); font-weight: 500; }}

  .gate {{
    display: flex; align-items: center; gap: 14px; border-radius: 12px; padding: 16px 20px; margin: 22px 0 24px;
    border: 1px solid var(--gate-color); background: var(--gate-soft);
  }}
  .gate-blocked {{ --gate-color: var(--crit); --gate-soft: var(--crit-soft); }}
  .gate-clear {{ --gate-color: var(--ok); --gate-soft: var(--ok-soft); }}
  .gate-dot {{ width: 12px; height: 12px; border-radius: 50%; background: var(--gate-color); flex: none; }}
  .gate-title {{ font-size: 1.05rem; font-weight: 600; color: var(--gate-color); }}
  .gate-sub {{ font-size: 13px; color: var(--ink-muted); margin-top: 2px; }}
  .risk-badge {{
    margin-left: auto; flex: none; text-align: right; padding: 6px 14px; border-radius: 8px;
    border: 1px solid var(--risk-color); background: var(--risk-soft);
  }}
  .risk-badge .risk-level {{ font-size: 0.85rem; font-weight: 700; color: var(--risk-color); letter-spacing: .03em; }}
  .risk-badge .risk-pct {{ font-size: 11.5px; color: var(--ink-muted); margin-top: 1px; }}

  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 0 0 26px; }}
  .stat {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px;
    display: flex; align-items: center; gap: 12px; cursor: pointer; text-align: left; font: inherit; color: inherit;
    transition: border-color .15s ease;
  }}
  .stat:hover {{ border-color: var(--stat-color, var(--accent)); }}
  .stat.active {{ border-color: var(--stat-color, var(--accent)); box-shadow: 0 0 0 1px var(--stat-color, var(--accent)) inset; }}
  .stat-bar {{ width: 5px; align-self: stretch; border-radius: 3px; background: var(--stat-color, var(--ink-faint)); flex: none; }}
  .stat-label {{ font-size: 12px; color: var(--ink-muted); }}
  .stat-count {{ font-family: "IBM Plex Mono", monospace; font-size: 22px; font-weight: 600; line-height: 1.1; }}

  .section-label {{
    font-family: "IBM Plex Mono", monospace; font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
    color: var(--ink-faint); margin: 0 0 10px;
  }}

  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }}
  .tile {{
    display: flex; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden;
    transition: border-color .15s ease, transform .1s ease;
  }}
  .tile:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
  .tile-blocking {{ border-color: var(--crit); box-shadow: 0 0 0 1px var(--crit) inset; }}
  .tile-attention {{ border-color: var(--high); }}
  .tile-bar {{ width: 5px; flex: none; background: var(--ink-faint); }}
  .tile-blocking .tile-bar {{ background: var(--crit); }}
  .tile-attention .tile-bar {{ background: var(--high); }}
  .tile-inventory .tile-bar {{ background: var(--accent); }}
  .tile-icon {{
    width: 30px; height: 30px; flex: none; align-self: flex-start; margin: 13px 0 0 14px;
    display: flex; align-items: center; justify-content: center; overflow: hidden;
  }}
  .tile-icon:empty {{ display: none; }}
  .tile-icon svg, .tile-icon img {{ width: 100%; height: 100%; object-fit: contain; }}
  .tile-body {{ padding: 13px 15px; flex: 1; min-width: 0; }}
  .tile-icon:empty + .tile-body {{ padding-left: 15px; }}
  .tile-top {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
  .tile-title {{ font-size: 14.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .tile-service {{ font-weight: 400; color: var(--ink-muted); }}
  .blocker-flag {{
    font-size: 10px; font-weight: 700; letter-spacing: .04em; color: var(--crit); background: var(--crit-soft);
    border: 1px solid var(--crit); border-radius: 5px; padding: 2px 6px; flex: none;
  }}
  .tile-badges {{ margin-top: 9px; }}
  .tile-sub {{ margin-top: 7px; font-size: 12px; color: var(--ink-muted); }}

  .pill {{
    display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
    font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; white-space: nowrap;
    background: var(--pill-soft, var(--info-soft)); color: var(--pill-color, var(--info));
  }}

  .other-section {{ margin-top: 30px; }}
  .other-links {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .other-link {{
    font-size: 12.5px; padding: 6px 12px; border-radius: 8px; background: var(--surface); border: 1px solid var(--border);
    color: var(--ink-muted);
  }}
  .other-link:hover {{ border-color: var(--accent); color: var(--ink); }}

  .tile-empty {{ padding: 40px 0; text-align: center; color: var(--ink-faint); font-size: 14px; }}

  footer {{ margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--border); font-size: 12px; color: var(--ink-faint); }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow">DevSecOps Pipeline &middot; Run Report</div>
    <h1>Strata.Cloud CI Report</h1>
    <div class="meta">
      <b>Run</b> {esc(args.run_id)} &middot; <b>Ref</b> {esc(args.ref_name)} &middot;
      <b>Commit</b> <span class="mono">{esc(args.sha[:7])}</span> &middot; <b>Generated</b> {esc(args.generated_at)}
    </div>
  </header>

  <div class="gate {gate_class}">
    <div class="gate-dot"></div>
    <div><div class="gate-title">{esc(gate_title)}</div><div class="gate-sub">{esc(gate_sub)}</div></div>
    <div class="risk-badge" style="--risk-color:var(--{risk_color(risk_level)});--risk-soft:var(--{risk_color(risk_level)}-soft)">
      <div class="risk-level">Risk: {esc(risk_level)}</div>
      <div class="risk-pct">{esc(risk_note)}</div>
    </div>
  </div>

  <div class="section-label">Findings by severity (all checks)</div>
  <div class="stats" id="stats">{stat_buttons}</div>

  <div class="section-label">Reports -- click through for the exact file/line</div>
  <div class="grid" id="grid">{tiles_html}{empty_html}</div>

  {other_html}

  <footer>Every tile links to that check's own report (search, filter, and per-finding detail). Raw SARIF/SBOM for each tool is included alongside its HTML report in this same bundle.</footer>
</div>

<script>
(function () {{
  var active = null;
  var stats = document.querySelectorAll('.stat');
  var tiles = document.querySelectorAll('.tile:not(.tile-inventory)');
  stats.forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      var sev = btn.getAttribute('data-filter-sev');
      active = active === sev ? null : sev;
      stats.forEach(function (b) {{ b.classList.toggle('active', b === btn && active !== null); }});
      tiles.forEach(function (t) {{
        t.style.display = (!active || t.getAttribute('data-worst') === active) ? '' : 'none';
      }});
    }});
  }});
}})();
</script>
</body>
</html>
"""

    out_path = pathlib.Path(args.out)
    out_path.write_text(page, encoding="utf-8")
    print(f"Indexed {len(cards)} report(s), {len(other_links)} other artifact(s); "
          f"{len(blocking_cards)} blocking, {total_findings} total finding(s).")

    # Written last, after the report itself is safely on disk -- see
    # --risk-output's own comment for why the actual gate is a separate,
    # later step rather than this script failing outright.
    if args.risk_output:
        pathlib.Path(args.risk_output).write_text(risk_level, encoding="utf-8")

    if args.counts_output:
        # Per tool+service, so "HIGH: 3" in the severity table above can be
        # traced to exactly which check(s) it came from without opening the
        # report bundle -- this is the direct answer to "which check is
        # having the critical/high issue, which tool found it." Sorted
        # worst-first (critical desc, then high, medium, low) so the source
        # most responsible for the run's risk level sorts to the top.
        by_source = [
            {
                "tool": c["tool"],
                "service": c["service"] or "-",
                "critical": c["severity_counts"].get("critical", 0),
                "high": c["severity_counts"].get("high", 0),
                "medium": c["severity_counts"].get("medium", 0),
                "low": c["severity_counts"].get("low", 0),
                # This tool+service's OWN pass/fail against ITS OWN gate
                # (see TOOL_BLOCKING_SEVERITIES) -- independent of every
                # other row here, so a reviewer can see exactly which
                # check(s) actually failed rather than inferring it from
                # the run's single overall risk_level.
                "status": "FAIL" if c["blocking"] else "PASS",
            }
            for c in cards
            # Every gate-capable tool gets a row even with zero findings,
            # showing PASS explicitly -- a clean Checkov run is worth
            # stating, not just omitting, so a reviewer sees a real
            # per-check status for every scan that ran, not only the ones
            # that happened to find something.
            if not c["is_inventory"] and (c["count"] > 0 or c["is_blocking_tool"])
        ]
        by_source.sort(key=lambda r: (r["status"] != "FAIL", -r["critical"], -r["high"], -r["medium"], -r["low"]))

        # Individual findings -- one row per actual result, not just a
        # count, so the gate output can point straight at a rule ID and a
        # file/line the same way clicking through to the tool's own HTML
        # report would. Capped and severity-sorted (critical/high first)
        # since a run with dozens of low findings shouldn't bury the two
        # that actually failed the gate.
        MAX_FINDINGS_SHOWN = 50
        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        detail_rows = []
        for c in cards:
            if c["is_inventory"]:
                continue
            for f in c["raw_findings"]:
                sev = (f.get("severity") or "info").lower()
                if sev not in sev_rank:
                    continue
                loc_file = f.get("file") or ""
                loc_line = f.get("line")
                location = f"{loc_file}:{loc_line}" if loc_file and loc_line else (loc_file or "unknown")
                message = (f.get("message") or f.get("title") or "").replace("|", "\\|").replace("\n", " ").strip()
                if len(message) > 150:
                    message = message[:150] + "..."
                detail_rows.append({
                    "tool": c["tool"],
                    "service": c["service"] or "-",
                    "severity": sev.upper(),
                    "rule": f.get("ruleId") or "",
                    "location": location,
                    "message": message,
                })
        detail_rows.sort(key=lambda r: sev_rank.get(r["severity"].lower(), 9))
        shown_findings = detail_rows[:MAX_FINDINGS_SHOWN]

        # "breach" reflects the real per-tool gate decision (did ANY
        # card individually breach at this severity), not a recomputed
        # pooled total vs. threshold -- those two can disagree once a
        # threshold is raised above 0 for testing (three tools each one
        # under their own limit can still sum past a pooled comparison
        # despite nothing actually failing the gate).
        breached_this_run = {s for c in other_blocking for s in c["breached_severities"]}
        counts_payload = {
            "risk_level": risk_level,
            "severities": [
                {
                    "severity": sev.upper(),
                    "found": totals[sev],
                    "allowed": thresholds[sev],
                    "breach": sev in breached_this_run,
                }
                for sev in SEV_ORDER
                if sev != "info"
            ],
            "by_source": by_source,
            "findings": shown_findings,
            "findings_total": len(detail_rows),
            "findings_omitted": len(detail_rows) - len(shown_findings),
        }
        pathlib.Path(args.counts_output).write_text(
            json.dumps(counts_payload, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
