# Security Gate Runbook

Operating reference for the DevSecOps severity gate on Strata.Cloud's
Application CI/CD pipeline (`ci.yml`/`deploy-uat.yml`). For the pipeline as a
whole, see [README.md](README.md); this file covers only the security gate
— what it checks, how to read its output, and how to change it safely.

## Architecture: two checks, one policy

Findings from every scanner (Semgrep, CodeQL, Trivy filesystem/image, Grype)
are summed into one severity total per run and checked against one set of
thresholds, in exactly one script (`.devsecops/report-template/build_index.py`).
That check runs at two points in the pipeline, not one:

1. **SecOps Gate** (`reusable-secops-scan.yml`) — fail-fast, right after
   Semgrep/CodeQL/Trivy-fs finish, before any image is built. Catches a run
   that's already over a limit before spending several minutes building,
   scanning, signing, and pushing an image nothing downstream will deploy.
2. **Enforce risk gate** (`reusable-notify.yml`, the `Publish Report` job) —
   the full check, after Trivy (image) and Grype have also run. This is the
   real safety net: a run can pass stage 1 and still fail here if the image
   scan turns up something the filesystem scan couldn't see.

No individual scanner ever fails its own job over a finding it made
(Semgrep/CodeQL/Trivy/Grype all exit clean regardless of what they find) —
only these two gate checks decide pass/fail. The one exception is
**Gitleaks**, which fails its own job outright on any leaked secret,
independent of the severity count below.

## Threshold policy

| Severity | Threshold | Meaning |
|---|---|---|
| Critical | **0** | A single critical finding anywhere in the run fails it. |
| High | **0** | A single high finding anywhere in the run fails it. |
| Medium | 10 | Up to 10 medium findings across the whole run are tolerated. |
| Low | 100 | Up to 100 low findings across the whole run are tolerated. |

**Critical and High must always be 0 in the concluded, steady state.**
These are the production values and the standard this pipeline is meant to
enforce — not a target to negotiate down.

Thresholds may be temporarily raised for a specific, controlled purpose —
e.g. proving the rest of the pipeline (build, deploy, migrate) still works
end-to-end while a known, already-triaged finding is pending a fix, or
demonstrating the gate's reporting for a review. Any such change:

- must be reverted to Critical `0` / High `0` before the branch is
  considered concluded or merged;
- should reference the specific run(s) it was used for (see "Reference
  run" below), so a later reader isn't left wondering why a threshold was
  ever anything but zero.

Values live as repository Variables, not hardcoded in any workflow file:

```bash
gh variable list -R Wanda-Health/wanda-altcloud | grep THRESHOLD

# Temporarily raise, for a specific reason -- then revert:
gh variable set SECURITY_THRESHOLD_HIGH -R Wanda-Health/wanda-altcloud --body "5"
gh variable set SECURITY_THRESHOLD_HIGH -R Wanda-Health/wanda-altcloud --body "0"
```

Or via the UI: **Settings → Secrets and variables → Actions → Variables**.

## Reading the report

Every run that reaches either gate produces the same three tables —
**Severity totals**, **By tool / service**, **Individual findings** — in
three places:

- The failing step's own job log (`SecOps Gate` or `Enforce risk gate`).
- That job's entry in the run's **Summary** tab.
- The PR comment, on a `pull_request` event, which also shows a `SecOps
  Gate` and `Aggregate Severity Gate` row in its checks table so a reviewer
  can tell which of the two checks actually failed, without needing
  something in the table to already be red for it to make sense.

**Reading the checks table itself**: `Semgrep`, `CodeQL`, and
`Trivy (filesystem)` show that tool's **own worst finding**, summed
across every service it scanned — e.g. `❌ 2 High`, `⚠️ 3 Medium, 7 Low`,
or `✅ Clean` — sourced from the same data the findings table underneath
is built from, so the two can never disagree. This is deliberately
**not** gated by the current threshold: a tool row can read `❌ 2 High`
while `SecOps Gate`/`Aggregate Severity Gate` — the two rows that reflect
the actual pass/fail decision under whatever threshold is active right
now — still read `✅ Passed`, if High is temporarily raised for e2e
testing. None of these three ever fail their own row purely for finding
something; only a genuine tool crash does (shown as `❌ Crashed`).
`Gitleaks` is the one exception: it fails its own row on any leaked
secret, so `Passed`/`Failed` there is a real pass/fail, not a severity
summary.

The full per-tool HTML reports (search/filter/detail per finding) are in
the `ci-report-bundle` artifact on the run page.

## Reference run

<!-- Filled in from a real dispatched run, kept verbatim as the canonical
     example of what a full gate report looks like -- see the run linked
     below for the exact numbers and tables. Replace this run if a newer
     one supersedes it as the team's reference. -->

**Run:** _pending — filled in once the current end-to-end verification run
finishes; see that run's own report for the authoritative version until
this section is updated._

## Known limitation

Trivy (image) and Grype both scan the same built container image and can
each report the same real CVE independently — summing raw tool counts can
double-count one vulnerability as two findings. Deduplicating by CVE +
package + version is real future work, not implemented yet. Treat a count
close to a threshold with that in mind; a count well clear of it isn't
affected either way.

## Related

`wanda-altcloud-infra-code` runs the same class of gate over Terraform/IaC
scans (Checkov, Trivy config, Gitleaks) with the same threshold values and
the same "Critical/High must be 0" policy, via a single aggregate check
rather than this repo's two-stage fail-fast design — see that repo's own
`.github/workflows/RUNBOOK.md` for its specifics.
