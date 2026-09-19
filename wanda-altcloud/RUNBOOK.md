# Pipeline Runbook

This is the plain-language reference for how code gets from a developer's
branch to something actually running in AWS, for Strata.Cloud's five
services (`booking`, `connect`, `engine-auth`, `terminology`, `engine`).
It's written for anyone who needs to understand what the pipeline does and
why — a new engineer, a reviewer, a manager asking "what actually happens
when someone pushes code" — not just for someone already fluent in the
YAML. If you want the exact thresholds, commands, and file paths for the
security gate specifically, that lives in its own document:
[`.github/workflows/RUNBOOK.md`](.github/workflows/RUNBOOK.md). This one
is the bigger picture everything else sits inside.

## What problem this pipeline actually solves

Five independent services share one database and one deployment target.
Nobody wants to build, scan, and deploy each one by hand, and nobody wants
a security scanner's opinion to be the only thing standing between a typo
and production. So the pipeline does three jobs at once: it proves a
change works (tests), it proves a change is safe (five different security
tools, one policy), and it proves an artifact ships as exactly what got
scanned (immutable tags, a signature, a bill of materials) — and it does
all three automatically, the same way, every single time, so nobody has to
remember to run a scanner manually before a deploy.

## The shape of it, start to finish

A push or a pull request kicks off the same sequence regardless of which
service changed:

```
Code pushed / PR opened
        |
        v
  App Tests             SecOps Scan
  (does it work?)   (Semgrep, CodeQL, Trivy,
        |             Gitleaks + a fail-fast
        |              severity check)
        +----------+----------+
                   |
                   v
        Build, Scan & Push
   (build the image, scan it twice
    more, generate a bill of materials,
    sign it, push it to ECR)
                   |
                   v
         Publish Report / Security Gate
  (every finding from every tool, summed
   into one decision: does this run proceed?)
                   |
                   v
              Run Migrations
       (schema changes, before anything
            starts using the new image)
                   |
                   v
              Deploy to ECS
     (roll out the new version, wait
          for AWS to call it healthy)
```

Two things about this that aren't obvious from the diagram. First, App
Tests and SecOps Scan run **in parallel** — neither waits on the other —
so a run fails fast on whichever problem exists, instead of waiting
through a full test suite just to find out a scanner had already found
something ten minutes earlier. Second, a pull request or a feature-branch
push only ever goes through the first two boxes. Nothing gets built,
scanned twice, signed, migrated, or deployed until the change is actually
on `develop` (or dispatched by hand) — so a PR check gives fast feedback
without spending five minutes building five container images for a change
that might not even get merged.

## Testing: does it work?

**App Tests** does something a lot of pipelines skip: it actually starts
each changed service and confirms it responds, rather than trusting that
green unit tests mean the service boots. The full automated test suite and
code-quality checks (linting, type checking, a first pass of security
analysis) are built and wired into this pipeline already, but deliberately
left switched off for now — they cover tooling the client hasn't signed
off on yet through their own change-control process. Turning them on later
is a one-line change, not new engineering.

## Security: five tools, one decision

This is the part of the pipeline most likely to be misread if you only
glance at it, so it's worth being precise. Four tools look for different
classes of problem — **Semgrep** and **CodeQL** for insecure code
patterns, **Trivy** for known vulnerabilities in dependencies and, later,
in the built container image itself, and **Grype** as a second,
independent opinion on that same image. A fifth, **Gitleaks**, looks for
secrets that shouldn't be in the code at all.

Here's the part that trips people up: **none of those five tools can fail
their own job just because they found something.** A scanner's job only
fails if the tool itself crashes — a bad exit code, a timeout, a broken
download. That's deliberate: it means "this scanner's row is red" always
means something actually went wrong with the tool, never "the tool did its
job and found a problem," which would otherwise be indistinguishable from
a real infrastructure failure in the same table. Every finding, from every
tool, is still recorded — nothing is ignored — it just isn't what decides
pass or fail at the individual scanner level.

What actually decides pass or fail is one policy, checked twice:

- **Critical or High finding, anywhere, from any tool, on any service** —
  the run fails. Zero tolerance.
- **More than 10 Medium findings, summed across everything** — the run
  fails.
- **More than 100 Low findings, summed across everything** — the run
  fails.

That check runs once early (right after Semgrep/CodeQL/Trivy find
everything they're going to find, before a single container gets built —
so a run that's already over the limit doesn't waste several minutes on a
build nobody's going to deploy) and once again at the end, after the
container image itself has been scanned twice more. A run can pass the
early check and still fail the later one, if the image scan turns up
something the source-code scan couldn't see — that's expected, not a bug.
Gitleaks is the one exception to everything above: any leaked secret fails
its own job immediately, on the spot, independent of this count-based
policy entirely. A live credential in the repo isn't a number to weigh
against a threshold.

The exact thresholds, how to read the report, and the full operating
detail for this gate live in
[`.github/workflows/RUNBOOK.md`](.github/workflows/RUNBOOK.md) — including
one limitation worth knowing about even at this level: Trivy and Grype
both scan the same built image, so a single real vulnerability can
occasionally get counted twice. That's a known, accepted gap for now, not
an oversight.

## Build, sign, and prove it

Once a change clears both tests and the fail-fast security check, each
changed service gets its own container image built, scanned (Trivy and
Grype again, this time against the actual image rather than just its
source), and pushed to that service's own ECR repository. Two things
happen alongside the push that matter more than they might look like on
first read. The image gets **signed** with Cosign, using short-lived
credentials rather than a stored key, so anyone can later verify that
what's running in AWS is exactly what this pipeline built — not something
that got hand-pushed or swapped in after the fact. And a full **software
bill of materials** gets generated for it, so if a new CVE comes out
tomorrow for some library buried three dependencies deep, the question
"are we affected, and where" has an immediate answer instead of a
week of archaeology.

Every image is tagged with its branch and commit SHA, and that tag is
**immutable** — ECR will refuse to let a second push overwrite it. That's
a deliberate safety property, not friction: it's the guarantee that the
image sitting in ECS labeled with a given commit is actually the image
built from that commit, always, with no exceptions. The practical
consequence is that re-running the pipeline against a commit that's
already been built once will fail on the push step for any service that
already has an image at that SHA — which is correct behavior, not a
pipeline defect, and the fix is a new commit, not a mutable tag.

## Migrate, then deploy — in that order, on purpose

Database migrations run as their own step, before deployment, because the
new image's schema expectations need to already be in place before any
service instance starts using it. Only after migrations succeed does
deployment actually happen: AWS ECS rolls the new version out and this
pipeline waits for AWS itself to confirm the new tasks are healthy before
calling the deploy successful — a deploy step exiting cleanly is never, on
its own, treated as proof that anything is actually up and serving
traffic.

This is also the point in the pipeline where the security gate's decision
actually matters operationally, not just as a report. Migrations and
deployment are wired to depend on the gate genuinely passing — a run that
fails the severity check stops here, before a schema change or a new image
ever reaches a running environment. That dependency was tightened once
already after a real test proved a subtler version of it wasn't airtight:
a step that was merely *skipped* (because an earlier stage didn't run) was
initially treated the same as one that had *succeeded*, which is a real
gap in a gate meant to block bad deploys. It's since been corrected to
require an actual success, not just the absence of a recorded failure.

## Environments, and how a change actually reaches production

Four stages exist — `develop`, `uat/**`, `releases/**`, and `main`/prod —
and every change moves through them in a fixed order: a feature branch
merges into `develop`, `develop` promotes to a new `uat/**` branch, a
validated `uat/**` branch promotes to a new `releases/**` branch, and a
`releases/**` branch deploys to `main`/prod. No stage skips ahead — a
feature branch never merges straight into `uat/**`, and `develop` never
merges straight into `releases/**` or `main`. This is enforced in code,
not just convention: `cut-uat-branch.yml` hardcodes `develop` as the only
branch a `uat/**` branch can ever be cut from (no ref override input
exists), and `deploy-release.yml` only ever deploys the latest
`releases/**` branch, and only once its release PR is merged with every
check passed.

Unlike `develop` (one permanent branch), `uat/**` and `releases/**` are
**dated, short-lived branches** — a new one is cut for every promotion
(e.g. `uat/2026-09-10-cadcdf4`), not one long-lived branch reused forever.
Named `releases/**` (plural), not `release/**` — this repo separately has
an older, client-owned branch literally named `release` that predates
this pipeline, and git's ref-namespace rules do not allow `refs/heads/release`
and anything under `refs/heads/release/**` to coexist.

### How the image moves between stages — rebuild once, then promote

This is the part most worth reading carefully, because the two hops behave
differently on purpose.

**`develop` → `uat` rebuilds.** These are separate AWS accounts with no
shared registry, so the image built on `develop` was never reachable from
uat's account at all — confirmed by a real failed deploy before this was
caught. `deploy-uat.yml` therefore builds the same source commit again, scans
it, signs it, and pushes it to uat's own ECR as `uat-<sha>`.

**`uat` → `prod` does not rebuild. It promotes.** The exact image uat
built, scanned, signed and actually ran is copied into prod's registry
unchanged, as `prod-<short-sha>`. Nothing is rebuilt for production.

The distinction matters more than it might look. A rebuild produces an
image that *ought* to be identical — same source, so presumably the same
result. Promotion makes it a fact: the copy preserves the image's digest,
so the artifact running in production is byte-for-byte the one that was
tested, and the release fails outright if the digest changes in transit.
That is what turns "what we tested is what's running" from a reasonable
assumption into something provable after the fact.

Two things make this work, and both matter:

- The copy is done with `cosign copy`, which transfers the manifest bytes
  rather than re-uploading a rebuilt image. That preserves the digest —
  which is what keeps the **existing signature valid** — and carries the
  signature artifact across with it. A plain `docker pull` / `tag` / `push`
  would leave the signature behind in uat and land an *unsigned* image in
  production.
- Prod's build role is granted **read-only** access to uat's registry: pull
  and describe, plus `kms:Decrypt` restricted to ECR (uat's repositories are
  encrypted with a customer-managed key, so registry permission alone is not
  enough). No statement anywhere grants prod the ability to write into uat.

The practical consequence for anyone operating this: **a production deploy
can only ship something uat actually deployed first.** If no `uat-<sha>`
image exists for the commit being released, the promotion step fails and
production is never touched. There is no path that builds a fresh,
never-tested image straight into prod.

### The actual promotion mechanism, step by step

Every promotion follows the same two-action pattern: a human **dispatches**
a "Cut ___ Branch" workflow (no inputs at all — it works out both the
branch name and, for a release, which uat branch to release), which
creates the branch and opens the PR automatically; a human then
**merges** that PR, which is the real trigger for the rebuild and deploy.
A human never hand-creates a `uat/**` or `releases/**` branch, or
hand-opens its promotion PR.

#### How a promotion PR is built, and why the branch lags

The PR is a real `develop` → `uat/**` pull request (and a real
`uat/**` → `releases/**` one for a release). Getting that required one
non-obvious choice, and it has an operational consequence worth
understanding before changing anything here.

GitHub refuses to open a PR with zero commits between base and head. If
`uat/<date>-<sha>` were cut as an exact copy of `develop`'s tip — the
obvious implementation — the PR would have nothing to merge. An earlier
version worked around that with a throwaway `promote/**` branch carrying
a single empty commit, purely to manufacture a diff; the review showed
nothing, and the branches were clutter.

So each new branch starts from **the last commit the previous branch of
its own kind and the source have in common** (their `git merge-base`).
The PR's diff is then everything added since the last promotion — a
genuine, reviewable change set.

Why the merge-base, and not the previous branch's tip as an earlier
version used: the merge-base is an ancestor of the source, so merging the
PR produces **exactly** the source's tree. Nothing committed straight onto
an old branch can ride along, and the PR can never conflict. Cutting from
the previous tip inherited everything on it, and that went wrong for real
— an obsolete `ci-release.yml` was committed onto a release branch, merged
back into uat branches while a conflict was resolved in GitHub's web
editor, spread into every later branch without a single conflict, and ran
migrations against the production database. It still sits on
`uat/2026-09-14-8d95404`, `uat/2026-09-14-d770220` and both `releases/**`
branches; cutting from the merge-base is what leaves it behind.

The release cut additionally refuses if the uat branch being released, or
the commit the new release branch would start at, contains a workflow
file `develop` has never had. Creating a branch and merging a PR are both
pushes, and each runs whatever workflows are in that commit — which is
exactly how `ci-release.yml` ran.

The consequence: **between being cut and its PR merging, a `uat/**`
branch still holds older code.** Two things follow, and both are
load-bearing rather than incidental:

- `deploy-uat.yml`'s `github.actor != 'github-actions[bot]'` guard is what
  stops the branch-creation push from deploying. Without it, cutting a
  branch would roll UAT *backwards*. Do not remove it as dead code.
- **Only a `uat/**` branch whose PR has merged may be released.** The
  release cut enforces this by selecting on the deploy tag.

`deploy-uat.yml` also deploys **only the latest uat branch** — the one the
most recent Cut UAT Branch run created — and only when the push is that
promotion PR's merge with every check on it passed. Merging an older
promotion PR that was left open, or committing straight onto a uat
branch, deploys nothing and the run says why. Close superseded promotion
PRs instead of merging them.

1. **Feature → develop**: normal PR, gated by `ci.yml`'s pull_request
   checks (App Tests + SecOps Scan).
2. **Cut to UAT**: dispatch **Cut UAT Branch** (`cut-uat-branch.yml`, no
   inputs — always snapshots `develop`'s current HEAD). It creates
   `uat/<date>-<sha>` and opens a promotion PR into it.
3. **Promote to UAT**: merge that PR. `deploy-uat.yml` (triggered on push to
   `uat/**`, ignoring the bot's own branch-creation push) rebuilds, scans,
   pushes to UAT's ECR, runs migrations, and deploys to UAT.
4. **Test in UAT.**
5. **Cut a release**: dispatch **Cut Release Branch**
   (`cut-releases-branch.yml`) — **no inputs**. It releases the most
   recently *deployed* uat branch, creates `releases/<date>-<sha>` and
   opens a release PR into it.
6. **Review and merge the release PR.** Opening it runs the full App Tests
   + SecOps Scan gate against the release candidate. Merging it builds and
   deploys **nothing** — it records that this release was reviewed. Prod is
   still untouched at this point.
7. **Deploy to prod**: dispatch **Deploy - Release (Prod)**
   (`deploy-release.yml`) **from `develop`** — the same workflow, name and
   choices as summit's — picking `build-only` (a dry run: confirms the
   release is ready and every image it needs is in uat's registry, then
   stops) or `deploy`. Nobody picks a branch: it always takes **the latest
   release** — the one step 5 created — and refuses if its release PR
   isn't merged, if any check on it failed or is still running, or if
   there is no release at all. It never falls back to an older release.
   On `deploy` it promotes each service's signed image from uat's registry
   into prod's, runs the **prod database migrations** from the release
   commit, deploys all five services and smoke-tests the engine through
   prod's public URL, then tags the release `release-<date>-<sha>`. It
   deploys immediately on dispatch; the only gate is the `prod`
   Environment's required reviewers, of which there are none yet.
8. **`main` is not updated by the deploy.** An earlier `sync-main` job
   pushed straight to `main`, which `main`'s ruleset refuses every time;
   it has been removed. `main` stays the source of truth through PRs.

Each environment is its own isolated set of AWS resources — its own ECR
repositories, its own AWS account entirely for `uat`/`prod` versus
`develop` — never one shared pool of images or infrastructure split by
convention alone. All three environments (`develop`, `uat`, `prod`) have
now been proven working end to end against real infrastructure, including
a real, successful `prod` deploy of the infra layer (see the infra repo's
own runbook) — the backend application's own first `prod` deploy is
staged and ready, pending the approvals described below.

### Doing this by hand in GitHub's UI, step by step

Everything above is doable entirely through the GitHub website — no
command line needed for ordinary promotion work. This is the exact
click-path for each step.

**Cutting a UAT branch**

1. Repo → **Actions** tab → **Cut UAT Branch** in the left sidebar.
2. Click the **Run workflow** dropdown, top right.
3. Pick **`develop`** in "Use workflow from". This one does matter: a
   `workflow_dispatch` runs *that ref's* copy of the file, and a `uat/**`
   branch carries an older copy. (The branch it cuts always comes from
   `develop`'s HEAD regardless; it is the workflow's own code that differs.)
4. No inputs at all. Click the green **Run workflow** button.
5. Refresh the Actions list after ~10-20s -- a new run appears and goes
   green.
6. Go to the **Pull requests** tab. A PR has appeared automatically,
   titled "Promote develop (`<sha>`) to UAT", **from `develop` into the
   new `uat/<date>-<sha>` branch**, showing the real commits added since
   the last promotion. Nobody hand-creates the branch or the PR.
7. Review it (App Tests + SecOps Scan run automatically as PR checks),
   then click **Merge pull request**.
8. That merge is the real trigger: **Actions → Deploy - UAT** starts a run
   that rebuilds, scans, pushes, migrates, and deploys to UAT for real.

**Cutting a release branch** (once UAT is confirmed good)

1. **Actions** → **Cut Release Branch** in the sidebar.
2. **Run workflow** dropdown -- again pick **`develop`**, for the same
   reason as above.
3. **No inputs.** Click **Run workflow**. Which uat branch is released is
   worked out for you (see below), not typed in.
4. Check **Actions** for the run to go green, then **Pull requests** for
   the new PR -- **from the selected `uat/**` branch into the new
   `releases/<date>-<sha>` branch**.
5. Review and merge it. Opening the PR runs the full App Tests + SecOps
   Scan gate; merging it builds and deploys **nothing**. Prod is still
   untouched — deploying it is the separate, deliberate dispatch below.

**How the release cut picks its uat branch, and why it isn't the newest**

It selects the most recent `uat/**` branch carrying a
`uat-<date>-<shortsha>` tag, which `deploy-uat.yml` writes **only after its
deploy job succeeds**. Recency alone would be actively unsafe: a `uat/**`
branch holds the *previous* release's code from the moment it is cut
until its promotion PR merges, so the newest branch by date is routinely
one that was never deployed, and releasing from it would quietly ship the
previous release. Selecting on "what actually deployed" rather than "what
was created last" is the whole point. If nothing has been deployed, the
run fails with a message saying so rather than guessing.

**Deploying to prod**

1. Make sure the release PR from the step above is **merged** and every
   check on it is green.
2. **Actions** → **Deploy - Release (Prod)** → **Run workflow**.
3. **Use workflow from: `develop`.** Dispatched from anything but
   `develop` or `main` it refuses, because a copy on another branch could
   have been modified.
4. **action**: `build-only` first if you want a dry run, then `deploy`.
   There is nothing else to fill in — which release is worked out for you.
5. Click **Run workflow**. `deploy` goes **immediately**. The first job's
   summary names the release branch, its PR, and the uat commit being
   promoted. If the image for that commit isn't in uat's registry —
   because it was never actually deployed to UAT — it stops there, before
   the database or any service is touched.

One honest caveat, repeated from "What's genuinely still open" below:
this last step currently has **no GitHub-enforced human approval gate**
(no required reviewers configured on the `prod` Environment yet) --
dispatching it runs immediately, with only the deliberate act of picking
"Run workflow" standing in for review.

### Branch cleanup

There are no longer any `promote/**` or `promote-release/**` branches to
clean up — that mechanism is gone (see "How a promotion PR is built"
above). Only the `uat/**` and `releases/**` branches themselves
accumulate, and they are not auto-deleted: cleanup is left to the
responsible person's judgment per branch rather than forced. Expect the
branch list to grow and prune it periodically.

Two cautions when pruning, both of which have bitten already:

- **Do not delete the `uat/**` branch a release was cut from** while that
  release is still the current one. The release cut selects its source by
  the deploy tag on a uat branch's tip, so deleting that branch removes
  the pipeline's own record of what was last released. (The tag survives
  the branch, but the selection looks at branches.)
- **Never commit directly to a `releases/**` branch.** A release branch
  is cut from the previous one, so any commit made straight onto it is
  inherited by every future release and collides with what the uat branch
  brings. That is not hypothetical: `releases/2026-09-12-cadcdf4` carries
  three such commits, and the release PR cut from it (#88) opened, passed
  every check, and was permanently unmergeable. The cut workflows now
  start new branches from the merge-base, so such commits are left behind
  rather than inherited — but the commits themselves still exist on those
  old branches, so never release from one by hand.

## Where this actually lives in AWS

Nothing in this pipeline holds a long-lived AWS key. Every step that talks
to AWS authenticates through GitHub's OIDC identity provider, assuming a
short-lived role scoped to exactly what that one step needs — one role for
pushing images, a separate one for deploying, so a compromise of one can't
reach into the other's permissions. Each service has its own ECR
repository (five services, five repositories, not one shared repository
juggling five unrelated images under one name), and each repository has
tag immutability turned on for exactly the reason described above.

## Running it day to day

Every real push and every pull request runs this pipeline automatically —
there's nothing to trigger by hand for ordinary work. When you do need to
run it manually — testing one service in isolation, or re-running past a
known, already-being-fixed failure — that's a standard GitHub Actions
manual dispatch: **Actions → CI → Run workflow**, choosing the branch,
target environment, and which services to include. `develop` also keeps
a manual single-service redeploy, **Deploy - Develop (manual)**, which can
pin an exact previous image. UAT and production deliberately have no such
side door, the same as summit: they change only through a merged
promotion or release PR, so a bad change is fixed forward on `develop`
and promoted again.

If a run fails, the first thing worth checking is *which* stage failed,
because the pipeline is deliberately built so different failures mean
different things: a red App Tests means the service itself is broken; a
red SecOps Gate or Aggregate Severity Gate means a real security policy
was breached (see the security-gate runbook for exactly which finding and
how to read it); a red Build, Scan & Push that isn't a severity gate is
almost always something more mundane — a Docker build error, an ECR
permission issue, or a push rejected because that exact commit was already
built once before.

## What's genuinely still open

This is worth stating plainly rather than leaving implicit.

- The full automated test suite and code-quality checks are built but
  switched off, pending the client's own change-approval process — see
  App Tests above.
- Post-deploy HTTP verification covers **one** service, not all five.
  `reusable-ecs-deploy.yml` runs `scripts/smoke-test.sh` against
  `HEALTH_URL_<SERVICE>/health` after each deploy, and `HEALTH_URL_ENGINE`
  is now set on `develop` to the public ingress
  (`https://<cloudfront-domain>/wanda`) — so every develop deploy of that
  service proves the whole public path still works end to end: CloudFront
  → ALB → the running container. The other four services have no such
  variable and log a skip warning instead, deliberately: unversioned
  `/health` matches no ALB listener rule, so it falls through to the
  default action and only ever reaches `strata-engine`'s target group.
  Pointing all five variables at that same URL would produce five green
  checks that all tested one service, which is worse than an honest skip.
  Covering the rest needs per-service health routing (a host-based split,
  or an app change so each service answers a distinct path) — not yet
  done. `HEALTH_URL_ENGINE` is set the same way on `uat`
  (`https://dw4s63rkp2mp1.cloudfront.net/wanda`) and `prod`
  (`https://d6bobh3t7w20z.cloudfront.net/wanda`), so their engine deploys
  run the smoke test rather than skipping it.

  Worth being clear this is an *extra* check, not the only one: every
  service is already health-checked continuously by its own ALB target
  group, and `wait-for-service-stability: true` means a deploy fails if
  those checks don't pass, so a broken service cannot silently ship
  regardless of the smoke test.
- The security gate's one known limitation — Trivy and Grype potentially
  double-counting the same vulnerability found in the same image — hasn't
  been resolved with real deduplication yet; an accepted gap, not an
  oversight.
- The cut workflows **cannot open their own promotion PRs yet.** This
  GitHub enterprise forbids the default Actions token from creating pull
  requests (`GitHub Actions is not permitted to create or approve pull
  requests`), and the repo-level override can't lift it — the API refuses
  with `409 The enterprise does not allow...`. Both `cut-uat-branch.yml`
  and `cut-releases-branch.yml` therefore cut their branches successfully
  and then fail at the PR step, which is why past promotions were done by
  hand. Both now read a **`PROMOTION_PAT`** repository secret in
  preference to the default token.

  **Use a classic token with the `repo` scope.** This is settled by
  experiment, not preference — the working secret in place today is a
  classic one, and it went in only after a fine-grained token failed.

  A fine-grained token is the better-scoped option in principle and is
  what to try first if the org's policy ever allows it, but it must name
  **`Wanda-Health` as its resource owner** (not a personal account, and
  not editable afterwards), be scoped to this repository, and carry
  *Pull requests: Read and write*. In this organisation it then sits in
  **"Pending owner approval"** until a client-side org owner clears it,
  which was not available. A classic token has no resource-owner concept
  at all: it acts as you and reaches any repository you can already
  reach, so it sidesteps that approval entirely. If the org enforces SAML
  SSO, click **Configure SSO / Authorize** on the token after creating
  it — easy to miss, and it fails silently without it.

  **Recognise the failure mode**, because it reads like something else
  entirely. A token without access produces:

  `GraphQL: Could not resolve to a Repository with the name 'Wanda-Health/wanda-altcloud'`

  — not a 403, because GitHub will not confirm a private repository
  exists to a caller that cannot see it. Read it as "this token has no
  access here", never as "the repository name is wrong". A
  fine-grained token created against a personal account produced exactly
  this, and it cost real debugging time.

  **Two operational caveats.** The promotion PRs are authored as whoever
  owns the token, so they stop working when that person leaves — for a
  long-lived handover a dedicated service account is the more durable
  owner than anyone's personal token. And every token carries an expiry:
  note the date somewhere visible, because the symptom when it lapses is
  that same confusing "could not resolve" error rather than anything
  saying "expired".

  Until the secret resolves, each cut still pushes its branch and then
  prints the exact compare URL to open the PR manually, so a promotion is
  never left half-done — but it is a manual step that shouldn't be one.
- `cut-uat-branch.yml`, `cut-releases-branch.yml`, and
  `deploy-release.yml` are all `workflow_dispatch`-only, which
  GitHub only allows to actually be triggered if the workflow file exists
  on the repo's default branch (`main`). None of the three were
  registered there until PRs #68 and #69 — before that, every past UAT
  promotion in this repo's history was done by someone manually
  replicating these files' git operations by hand, not by actually
  running them. Confirm both PRs are merged before relying on any of
  these as a real button. `deploy-release.yml` (the prod deploy, formerly
  `release-deploy-prod.yml`) and `deploy-uat.yml` (formerly `ci-uat.yml`)
  were renamed on 2026-09-15 to match summit. `deploy-release.yml` is
  **not on `main` yet**: until a `develop` → `main` replica PR carries it,
  **Deploy - Release (Prod)** has no Run workflow button.
- The `prod` GitHub Environment currently has **zero required
  reviewers** configured. `deploy-release.yml`'s own design assumes
  a human approval gate here (see "Environments" above) — until reviewers
  are added under Settings → Environments → prod, dispatching a prod
  deploy runs immediately with no GitHub-enforced checkpoint.
- **`main` is updated only by PR.** `deploy-release.yml` used to end
  with a `sync-main` job running `git push origin <sha>:refs/heads/main`.
  A repository ruleset on `main` enforces `deletion`, `non_fast_forward`
  and `pull_request`, so that push was refused every time and every full
  release would have finished with a failed job. That is correct
  protection for a default branch and the client's governance, so the job
  was removed rather than the ruleset loosened. `main`'s tree was made a
  replica of `develop`, with its previous history preserved under the tag
  `main-pre-replica-2026-09-14`; it drifts as `develop` moves, and
  re-syncing it means another replica PR.
- **The backend's first production deploy through this workflow has not
  run yet.** Before it, the only thing that ever touched production was
  the stray `ci-release.yml` on 2026-09-14: it built prod images and ran
  prod migrations, but never deployed a service, which is why prod's API
  answers 503.
- **A provisioned test user cannot sign in through the portal.**
  `deploy-uat.yml`'s "Provision test users" job runs and succeeds, but it only
  does the *database* half — it adopts a Cognito identity Terraform
  already created, writes the profile, assigns the role and syncs the
  Cognito group. Terraform creates those users with a **temporary**
  password, which leaves them in `FORCE_CHANGE_PASSWORD`, and Cognito
  then answers login with a `NEW_PASSWORD_REQUIRED` challenge that the
  login route does not handle. That is a known pending client-code change
  awaiting the client's own confirmation, not a misconfiguration. (Do not
  confuse it with the password-upgrade step the login page *does* have —
  that serves the legacy-migration 409 challenge, a different mechanism.)

  To get a usable login, dispatch **Provision Client Users** with
  `environment: uat`. It reads the `CLIENT_USERS_CSV` secret of that
  environment (columns `Email Address`, `First Name`, `Last Name`, `Role`,
  optional `Password`), creates any missing account with a **permanent**
  password, deliberately sidestepping the challenge, and proves each new
  account with a real login. A generated password is written only to the
  task's CloudWatch log, never the job log; supply the `Password` column
  when the person running it has no CloudWatch access. Until 2026-09-15
  this workflow never checked out the repository, so it failed with exit
  127 before reaching AWS. `uat` has `CLIENT_USERS_CSV` and
  `LOGIN_BASE_URL` set as of that date.

  **Production is excluded by design**, twice: the workflow offers no
  `prod` option, and the provisioning script in `strata.engine.auth`
  refuses to run against prod. Creating production identities is a
  decision for the client, not something to route around.

- **Dependabot targets `develop`, deliberately.** It has no
  `target-branch` by default, which means the repo's default branch —
  `main`. That was harmless only while `main` was a stale snapshot with
  nothing to scan; the moment `main` became a replica, Dependabot opened
  ten PRs straight against it, bypassing the whole promotion chain and
  every gate (a PR into `main` runs no checks; a PR into `develop` runs
  App Tests and the full SecOps scan). All entries now set
  `target-branch: develop`. If updates ever start appearing against
  `main` again, that setting has been lost.

- **`aquasecurity/trivy-action` is used `@master`, unpinned**, so an
  upstream change lands here with no warning. It already has: the input
  was renamed `trivy-version` → `version`, and because an unrecognised
  input is only a *warning*, the old pin sat in the file looking correct
  while doing nothing. The binary version is now pinned to a real tag
  (`v0.74.0`) and its download authenticated via `token-setup-trivy`,
  since the intermittent failures were an anonymous GitHub API rate
  limit rather than a bad version. Pinning the action itself to a tag
  would prevent this whole class, at the cost of not picking up its
  updates automatically — not yet decided.

- **The uat → prod image promotion has not been exercised end to end
  yet.** The cross-account grants it needs are applied and live in both
  accounts (uat's ECR repository and KMS key policies, prod's
  `ecr-pull-promotion-source` identity policy), and the workflow logic is
  in place, but no real release has been deployed through it. The first
  one should be watched rather than assumed: confirm the promotion step
  pulls from uat, that the digest assertion passes, that prod migrations
  exit 0, and that `cosign verify` succeeds against the resulting prod
  image. A re-run after a partial failure skips images already promoted
  with the same digest, since prod's tags are immutable.

None of that changes what's already true today: every stage described
above has been run for real, against real infrastructure — real tests,
real scans, real deployments — not just written down and assumed correct.
