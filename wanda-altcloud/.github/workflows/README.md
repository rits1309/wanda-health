## Strata.Cloud CI/CD Pipeline

This is the automated pipeline that takes a code change to any of the five
Strata.Cloud services (`booking`, `connect`, `engine-auth`, `terminology`,
`engine`) from a pull request through to a running deployment on AWS —
testing, security scanning, signing, and deploying it, with no manual steps
in between except the approvals your team already requires.

## What happens on every change

```
Code pushed / PR opened
        |
        v
  App Tests             SecOps Scan
  (unit tests +          (Semgrep, CodeQL,
   quality checks)        Trivy, Gitleaks)
        |                     |
        +----------+----------+
                   |
                   v
        Build, Scan & Push
   (build the container, scan it,
    generate a software bill of
    materials, sign it, push to ECR)
                   |
                   v
              Deploy to ECS
     (roll out the new version,
      wait for it to be healthy)
                   |
                   v
           Report Published
    (one downloadable report covering
     every check that ran)
```

Testing and security scanning run in parallel and both have to pass before
anything gets built. A pull request or a feature-branch push only goes
through testing and scanning, for fast feedback — nothing gets built or
deployed until it's merged into `develop` or `uat`.

## What each stage checks

- **App Tests** — a real health check: the service is actually started and
  confirmed responding before this stage passes. The full automated test
  suite plus code quality checks (linting, type checking, basic security
  analysis) are built and ready in this pipeline, but intentionally left
  switched off for now — they cover new tooling the client hasn't signed
  off on yet via a Change Request. Turning them back on once that's
  approved is a one-line change, not new work.
- **SecOps Scan** — four independent security tools, each looking for a
  different class of problem: **Semgrep** and **CodeQL** for insecure code
  patterns, **Trivy** for known vulnerabilities in dependencies, and
  **Gitleaks** for secrets (passwords, API keys) that shouldn't be in the
  code. Only Gitleaks actually blocks the build — a leaked secret is a live
  incident, not a judgment call. Semgrep/CodeQL/Trivy findings are scanned in
  full, uploaded as SARIF/HTML reports on every run, and still show up as a
  warning annotation on the run — but by design they report on `strata-cloud/`
  (the client's own application code) rather than gate merges on it, since
  fixing that code is the client's call, not this pipeline's to enforce.
- **Build, Scan & Push** — the container image itself gets scanned twice
  more (Trivy and Grype, a second independent vulnerability scanner) before
  it's allowed anywhere near AWS. Every image also gets a signed,
  tamper-evident record (via Sigstore/Cosign) and a full software bill of
  materials, so exactly what's running can always be verified later.
- **Deploy** — rolls the new version out to AWS ECS and waits for AWS to
  confirm it's healthy before considering the deploy successful.
- **Report Published** — every scan produces a human-readable report,
  bundled into one download attached to that run.

## Environments and approvals

Three environments: `develop`, `uat`, and `prod`. Promoting to `prod`
requires a manual approval from a designated reviewer in GitHub before
anything deploys — that gate is enforced by GitHub itself, not something a
workflow file could accidentally skip.

Rolling back is a single manual action: redeploy any previous, already-built
image to a given environment on demand — no rebuild needed.

## Promoting to production

UAT and production deploy exactly the way summit's do, under the same
workflow names:

- **Deploy - UAT** (`deploy-uat.yml`) runs when the promotion PR into the
  latest `uat/**` branch is merged, and only then — it refuses a stale
  branch, a direct commit, or a PR merged over a failing check.
- **Deploy - Release (Prod)** (`deploy-release.yml`) carries no push or
  pull-request trigger at all. Someone dispatches it by hand, from
  `develop`, and picks `build-only` (a dry run) or `deploy`. It always
  takes **the latest release**: the `releases/**` branch the most recent
  Cut Release Branch run created, found through its release PR, and only
  once that PR is merged with every check passed. On `deploy` it promotes
  the exact signed images uat built and ran (no rebuild), runs the prod
  database migrations from the release commit, deploys every service, and
  tags the release `release-<date>-<sha>`. `main` is not touched; it is
  updated through PRs, since its ruleset refuses direct pushes.

Either action deploys through the same `reusable-ecs-deploy.yml` used by
`develop`/`uat`, called with `environment: prod` — that Environment setting
is what makes GitHub itself pause the job for a required reviewer. There's
no separate "approval" job in this file to bypass; the pause comes from the
Environment configuration, not from pipeline logic that could be edited
around.

## Provisioning client users

Real client accounts are never created automatically by a pipeline run —
that stays a deliberate, human-triggered action. `provision-client-users.yml`
is a separate `workflow_dispatch`-only workflow, runnable against `develop`
or `uat` (never `prod` — enforced by both the workflow's own environment
choice and a hard-coded guard in the script it calls). It reads the current
user list from that environment's `CLIENT_USERS_CSV` secret, skips anyone
who already exists, and creates only who's missing. Every brand-new account
is health-checked with a real login attempt against the live environment
before the run reports success — the underlying script exiting `0` is never
treated as proof on its own.

## Setting it up

Nothing in this pipeline uses long-lived AWS keys — every AWS step
authenticates via GitHub OIDC, assuming a short-lived role scoped to
exactly what that step needs (one role for pushing images, a separate one
for deploying, each least-privilege). To point this pipeline at a new AWS
account/environment, three things need to exist first:

1. **An ECR repository per service** (`booking`, `connect`, `engine-auth`,
   `terminology`, `engine`) for the built images to push to.
2. **An ECS Fargate cluster with one service per `strata.*` service**,
   already running with the application's environment variables and
   database/secrets configuration wired up — this pipeline deploys new
   *versions* of what's already running, it doesn't create the service or
   its configuration from scratch.
3. **Two IAM roles trusted for GitHub OIDC** (build/push, and deploy),
   plus the corresponding values — region, account, cluster/service
   names, image registry paths — recorded as GitHub Environment variables
   for `develop`/`uat`/`prod`. `develop` is fully configured today; `uat`
   and `prod` need the same set filled in with their own AWS values
   before they can receive a deployment.

## Running it

Every real push or pull request runs automatically — nothing to trigger by
hand for day-to-day work. To run it manually (e.g. to test one service in
isolation, or force a build past a known test failure while that failure is
being fixed separately), it's a standard GitHub Actions manual dispatch:
**Actions → CI → Run workflow**, choosing the branch, environment, and
which services to include.

On `develop` only, **Actions → Deploy - Develop (manual)** redeploys one
service and can pin an exact previous image. UAT and production have no
such side door, the same as summit: they change only through a merged
promotion or release PR, so a bad change is fixed forward on `develop` and
promoted again.

## What's already proven working

Every stage above has been run end-to-end against real infrastructure —
real test runs, real security scans, real deployments to AWS ECS — not
just written and assumed correct.

## What's left to finish

- A couple of AWS configuration values (service health-check URLs) aren't
  wired in yet — deploys succeed regardless, this only limits one extra
  verification step after each deploy.
- The automated test suite and code quality checks are switched off
  pending Change Request approval (see App Tests above). Two services
  have pre-existing failing tests on the application side, unrelated to
  the pipeline — worth fixing before those checks are turned back on, so
  turning them on doesn't immediately show two services failing.
- `uat` and `prod` need the same AWS configuration `develop` already has,
  once those environments are ready to receive deployments.
