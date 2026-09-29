# WandaHealth Infrastructure Runbook — Strata Cloud Platform WandaHealth Infrastructure Runbook — Strata Cloud Platform

| | |
|---|---|
| **Updates** | First Draft
| **Author** | Altcloud |
| **Version** | 1.0 |


---

## Table of Contents

| # | Section | Description |
|---|---|---|
| — | [Introduction](#introduction) | What this runbook is and its scope |
| — | [Who Is This Document For?](#who-is-this-document-for) | Intended audiences and what each gets from it |
| — | [How to Read This Document?](#how-to-read-this-document) | Where to start by task, and conventions used |
| 1 | [Infrastructure Overview](#1-infrastructure-overview) | Platform summary, request flow, backend services, environments |
| 1 | ↳ [AWS Control Tower (landing zone)](#aws-control-tower-landing-zone) | Multi-account foundation, baseline, guardrails |
| 2 | [Prerequisites & Access](#2-prerequisites--access) | Tooling, OIDC roles, required GitHub variables and secrets |
| 3 | [Component Inventory](#3-component-inventory) | Terraform modules and what each provisions |
| 4 | [Deployment Procedures](#4-deployment-procedures) | How the pipeline works, promotion, workspace, tfvars, backend |
| 4.1 | ↳ [How the infra pipeline works](#41-how-the-infra-pipeline-works-end-to-end) | Reusable workflows and the three pipeline stages |
| 4.2 | ↳ [Promotion strategy](#42-promotion-strategy) | develop → uat → prod triggers and approvals |
| 4.3 | ↳ [Workspace strategy](#43-workspace-strategy) | Workspace-per-environment naming model |
| 4.4 | ↳ [tfvars strategy](#44-tfvars-strategy) | S3-hosted vars, secrets handling |
| 4.5 | ↳ [Backend / state strategy](#45-backend--state-strategy) | Partial backend, per-account state buckets |
| 5 | [Security & Compliance](#5-security--compliance) | Scanners, thresholds, reporting, accepted-risk register |
| 5.1 | ↳ [What security scanning we do](#51-what-security-scanning-we-do) | Gitleaks, Checkov, Trivy |
| 5.2 | ↳ [Thresholds & the aggregate risk gate](#52-thresholds-and-the-aggregate-risk-gate) | Per-severity limits and gating |
| 5.3 | ↳ [Reporting](#53-reporting) | PR comment, job summary, HTML dashboard |
| 5.4 | ↳ [Accepted-risk register](#54-accepted-risk-register) | Suppressed Checkov / Trivy findings and reasons |
| 6 | [Operational Playbooks](#6-operational-playbooks) | Per-incident recovery and routine operations |
| 7 | [Backup, DR & State Management](#7-backup-dr--state-management) | RDS backups, Terraform state, import blocks |
| 8 | [Monitoring & Alerting](#8-monitoring--alerting) | CloudWatch, GuardDuty, WAF |
| 9 | [Teardown](#9-teardown) | Destroy workflow and prod-destroy safeguard |

---

## Introduction

This runbook is the operational source of truth for the WandaHealth Strata Cloud
Terraform, how changes are deployed through the CI/CD pipeline, and how to
operate and recover the system when things go wrong.

The infrastructure is defined entirely as code in this repository. Nothing in
production is expected to be changed by hand except the small set of documented
bootstrap prerequisites (see Section 2). Every routine change flows through the
pipeline, and every recovery procedure in this document is grounded in the real
behaviour of the code in this repo.

> **Scope:** this covers the Terraform-managed AWS infrastructure in this
> repository. It does not cover the Strata application source code, which lives
> in separate repositories and is deployed by its own pipeline.


## Who Is This Document For?

| Audience | What they get from this runbook |
|---|---|
| **Infrastructure / DevOps engineers** | The complete deployment, operations, and recovery reference — the primary users. |
| **Client operations staff** | Understanding of what runs where, how deployments happen, and who to escalate to. |
| **On-call responders** | The operational playbooks in Section 6 for resolving common incidents fast. |
| **New team members** | Onboarding: the architecture, environments, and access model in one place. |
| **Auditors / security reviewers** | The security posture and the accepted-risk register in Section 5. |

It assumes working familiarity with AWS, Terraform, and Git-based CI/CD. It does
not teach those tools — it documents how they are used on this specific project.

---

## How to Read This Document?

- **Read Sections 1–3 first** if you are new to the project. They give you the
  architecture, the access model, and the full component inventory.
- **Go straight to Section 4** when you need to deploy or run a plan/apply.
- **Go straight to Section 6** during an incident — each playbook is
  self-contained and starts with the symptom.
- **Section 5** is for security reviews and for understanding which findings are
  deliberately accepted versus which still block a merge.
- Use the **Table of Contents** below to jump to any section. Cross-references
  such as "see §6.2" point to the numbered sub-sections.

Conventions used throughout:

- Blockquotes (`>`) call out important caveats and known-issue rationale.

---

## 1. Infrastructure Overview

## 1. Infrastructure Overview

The platform is a containerized backend (AWS ECS Fargate) fronted by an
Application Load Balancer and an API Gateway HTTP API, with a Cognito-based
authentication layer, a PostgreSQL database on RDS, secrets in AWS Secrets
Manager (KMS-encrypted), and a static single-page-application (SPA) frontend
served from S3 through CloudFront.

### Request flow (high level)

```
Client
  → CloudFront (SPA static assets)         → S3 (frontend bucket)
  → API Gateway HTTP API (JWT authorizer)  → VPC Link → ALB (:80/:443)
        → ECS Fargate services (strata-engine-auth, strata-booking,
          strata-connect, strata-terminology, strata-engine)
              → RDS PostgreSQL
              → Cognito (identity)
              → Secrets Manager (app + DB secrets)
```


### Backend services and routing

Routing is path-based at the ALB. Each service registers its own routes under
`/v1`; the ALB routes by path prefix and priority (lower number = evaluated
first).

| Service | Port | Health check | Routes (path prefixes) | Priority |
|---|---|---|---|---|
| `strata-engine-auth` | 8000 | `/health` | `/v1/auth/*` | 10 |
| `strata-booking` | 8010 | `/health` | availability-patterns, unavailability-blocks/patterns, coaches, members, slots, appointments, cancellation-policies, alternatives, reschedule, admin | 20 |
| `strata-connect` | 8004 | `/health` | readings, device-registrations | 30 |
| `strata-terminology` | 8020 | `/health` | medications, procedures, diagnoses | 40 |
| `strata-engine` (default) | 8000 | `/health` | `/v1/*` (catch-all) | 50 |

> Each prefix is generated in two forms — the bare path (`/v1/members`) and the
> wildcard (`/v1/members/*`) — because AWS ALB `*` does not match the bare path.
> See the comments in `main.tf` (`locals.alb_services`) for the full rationale.

### Environments — three separate AWS accounts

| Environment | Branch trigger | Apply mode | State bucket config |
|---|---|---|---|
| `develop` | push/PR to `develop` | auto-apply on push | `backends/develop.hcl` |
| `uat` | push/PR to `uat` | auto-apply on push | `backends/uat.hcl` |
| `prod` | manual dispatch only | manual + environment approval | `backends/prod.hcl` |

Each environment lives in its **own AWS account** with its own dedicated
Terraform state bucket. The Terraform workspace name (`develop`/`uat`/`prod`)
drives resource naming via `local.name_prefix = "wandahealth-<env>"`.

### AWS Control Tower (landing zone)

The three environment accounts are **member accounts of an AWS Control Tower
landing zone** (multi-account organization managed centrally, not by this repo).
What this means operationally:

- **Account isolation is the primary blast-radius boundary.** develop / uat /
  prod are separate accounts governed by Control Tower, so a change or incident
  in one cannot reach another. This is why state buckets, OIDC roles, and
  secrets are all per-account.
- **Each account ships with a Control Tower baseline.** Newly enrolled accounts
  already contain the standard Control Tower baseline VPC and guardrails before
  this Terraform ever runs. This stack provisions its **own** dedicated VPC
  (`networking/vpc`) rather than reusing the baseline VPC — do not confuse the
  two when inspecting an account (e.g. a fresh uat account showed *only* the
  Control Tower baseline VPC and no application resources).
- **Guardrails and SCPs are enforced above this repo.** Control Tower applies
  preventive/detective controls (Service Control Policies, Config rules) at the
  organization level. If a Terraform apply is denied by an SCP, the fix is at
  the Control Tower / organization layer, not in this repo.
- **Account provisioning and enrollment** (new environments, OU placement) are
  performed in Control Tower / Account Factory, outside this pipeline. This repo
  assumes the account already exists and the bootstrap OIDC role (Section 2) is
  in place.

> Control Tower is the foundation this infrastructure sits on, but it is managed
> separately. Nothing in this repository creates, modifies, or destroys Control
> Tower itself.

---

## 2. Prerequisites & Access

### Tooling

| Tool | Version | Notes |
|---|---|---|
| Terraform | 1.7.0 | Pinned in the deploy workflow (`TF_VERSION`) |
| AWS CLI | v2 | For manual operations / recovery |
| GitHub CLI (`gh`) | latest | For PRs and manual workflow dispatch |

### How CI authenticates to AWS

CI does not use static AWS keys. Each pipeline assumes an environment-scoped IAM
role via GitHub OIDC:

- **`github-actions-infra-<env>`** — the role Terraform runs as.
  **This is a manual bootstrap prerequisite. It is NOT managed by Terraform.**
  It must be created by hand once per AWS account before any pipeline can run.
  (Managing it in Terraform creates a chicken-and-egg problem and a destroy would
  delete its own running credentials mid-run.)
  - Role name: `github-actions-infra-<env>`
  - Inline policy: `terraform-infrastructure-<env>`
  - Trust: OIDC federated to `token.actions.githubusercontent.com`,
    `sub = repo:Wanda-Health/wanda-altcloud-infra-code@*:environment:<env>*`,
    `aud = sts.amazonaws.com`
- **`github-actions-deploy-<env>`** — the role the application deploy pipeline
  assumes (develop uses the legacy suffix `-dev`). Also a manual bootstrap for
  develop/uat; prod's is created by `module.iam`.

### Required GitHub configuration (per environment)

Repository/environment **variables**:

| Variable | Purpose |
|---|---|
| `AWS_ACCOUNT_ID` | Target account |
| `AWS_REGION` | Target region |
| `AWS_INFRA_ROLE_ARN` | Role the deploy workflow assumes (falls back to `AWS_BUILD_ROLE_ARN`) |
| `AWS_APN_ID` | AWS Partner Central migration opportunity tag (required, no default) |
| `SECURITY_THRESHOLD_*` | Optional risk-gate thresholds (CRITICAL/HIGH/MEDIUM/LOW) |

Repository/environment **secrets**:

| Secret | Purpose |
|---|---|
| `STRATA_DEV_AUTH_SECRET` | Strata auth secret (passed to Terraform as `strata_uat_auth_secret`) |
| `STRATA_EDGE_SHARED_SECRET` | strata-connect edge shared secret |
| `COGNITO_INITIAL_USERS_JSON` | JSON array of emails for initial Cognito users (optional; defaults to `[]`) |
| `BC_API_KEY` | Optional Bridgecrew/Prisma key for accurate Checkov severities |

> **tfvars are never committed.** Per-environment `environments/<env>.tfvars`
> live in S3 and are fetched at pipeline runtime. A precheck job hard-fails the
> build if any `*.tfvars` file is ever committed.

---

## 3. Component Inventory

| Domain | Module | Provisions |
|---|---|---|
| Networking | `networking/vpc` | VPC + flow logs |
| | `networking/internet-gateway` | IGW |
| | `networking/subnets` | Public / private / database subnets |
| | `networking/nat-gateway` | NAT gateways (per AZ) |
| | `networking/route-tables` | Route tables + associations |
| | `networking/alb` | Application Load Balancer, target groups, listener rules |
| | `networking/api-gateway` | HTTP API, Cognito JWT authorizer, VPC Link |
| | `networking/security-groups` | ALB / ECS / RDS security groups |
| Compute | `compute/ecr` | Container image repositories |
| | `compute/ecs` | Fargate cluster, services, autoscaling, scheduled tasks, migration task |
| Database | `database/rds` | PostgreSQL instance, subnet group, credentials secret |
| Auth | `auth/cognito` | User pool, app client, domain, role groups, initial users |
| Security | `security/kms` | KMS key for encryption at rest |
| | `security/iam` | Task roles, deploy-role policy attachments |
| | `security/guardduty` | Threat detection |
| | `security/waf` | WAFv2 WebACL on the ALB |
| | `security/secrets-manager` | Application secrets |
| Storage | `storage/s3` | Assets + logs buckets |
| Frontend | `frontend` | S3 static hosting + CloudFront distribution + edge functions |
| Monitoring | `monitoring/cloudwatch` | Log groups, dashboards, alarms |

---

## 4. Deployment Procedures

### 4.1 How the infra pipeline works (end to end)

The pipeline is built from **reusable workflows** so all three environments run
identical logic. Each environment has a thin entrypoint workflow that calls the
same shared building blocks:

```
terraform-develop.yaml ┐
terraform-uat.yaml     ├─→ pre_check.yaml ─→ security_scan.yaml ─→ terraform.yaml
terraform-prod.yaml    ┘   (Prechecks)       (Security Scan)        (Deploy)
```

The three jobs run **in order**, each gating the next:

**Stage 1 — Prechecks (`pre_check.yaml`)**
- Fails the build if any `*.tfvars` / `*.tfvars.json` file was ever committed
  (real values live in S3, not git).
- Hard-blocks `destroy` on `prod` (`environment == prod && action == destroy`).

**Stage 2 — Security Scan (`security_scan.yaml`)** — see Section 5 for full
detail. Runs Gitleaks, Checkov, Trivy, then an aggregate risk gate + PR report.

**Stage 3 — Terraform (`terraform.yaml`)**, in this sequence:

| Step | What it does |
|---|---|
| Configure AWS credentials | Assumes the env's OIDC role (`AWS_INFRA_ROLE_ARN`, falling back to `AWS_BUILD_ROLE_ARN`). Session duration from `AWS_ROLE_DURATION_SECONDS` (uat set to 4h for long applies; others default to 1h). |
| Verify AWS identity | `aws sts get-caller-identity` — confirms the right account. |
| Fetch tfvars from S3 | `aws s3 cp s3://<tfvars_bucket>/environments/<env>.tfvars ...` |
| Terraform Init | `terraform init -backend-config="backends/<env>.hcl"` |
| Select Workspace | `terraform workspace select -or-create <env>` |
| Format Check | `terraform fmt -check -recursive` |
| Validate | `terraform validate` |
| Plan | `terraform plan -var-file=... -out=tfplan` (tee'd to `plan_output.txt`) |
| Upload Plan | Artifact `terraform-plan-<env>` (plan + output, 30-day retention) |
| Apply | `terraform apply -auto-approve tfplan` (only when `action == apply`) |
| Destroy | `terraform destroy -auto-approve` (only when `action == destroy`) |
| Output + Upload | `terraform output -json` → artifact `terraform-outputs-<env>` |

### 4.2 Promotion strategy

Promotion flow: **feature branch → PR → `develop` → `uat` → `prod`.**

| Environment | Trigger | Apply mode | Approval |
|---|---|---|---|
| `develop` | push / PR to `develop` | auto `plan` + `apply` | none (unrestricted by design) |
| `uat` | push / PR to `uat` | auto `plan` + `apply` | none (unrestricted by design) |
| `prod` | manual `workflow_dispatch` only | choose `plan` / `apply` / `destroy` | GitHub required-reviewers on the `prod` Environment |

- develop and uat are meant to apply automatically on merge — there is
  intentionally no hand-rolled actor-permission gate.
- prod never triggers on push. It runs only when a human dispatches it, and the
  apply waits on the `prod` GitHub Environment's required-reviewers protection.
- **Prod `destroy` is impossible** — hard-blocked in Stage 1.

### 4.3 Workspace strategy

- One Terraform **workspace per environment**: `develop`, `uat`, `prod`.
- The workspace name is the single source of environment identity:
  `local.env = terraform.workspace` and `local.name_prefix =
  "wandahealth-<env>"`. Every resource name and tag derives from it.
- The pipeline selects it with `terraform workspace select -or-create <env>`,
  so a brand-new environment self-initializes.
- Some resources branch on the workspace (e.g. import blocks scoped to
  `develop`/`uat`, prod-only IAM roles) — always confirm you are in the correct
  workspace before a manual run.

### 4.4 tfvars strategy

- **tfvars are never committed to git.** Stage 1 fails the build if they are.
- Real per-environment values live in each environment's **own S3 bucket**
  (`environments/<env>.tfvars`) and are fetched at pipeline runtime.
- Secrets are **not** in tfvars — they come from GitHub secrets and are passed
  as `-var` at plan/apply time (`strata_uat_auth_secret`,
  `strata_edge_shared_secret`, `cognito_initial_user_emails`).
- The legacy variable `strata_dev_auth_secret` is kept declared (unused) so old
  S3 tfvars that still reference it don't error.

### 4.5 Backend / state strategy

- `backend.tf` is a **partial** S3 backend — `key` and `encrypt` are fixed, but
  `bucket`/`region` are deliberately left out because each environment is a
  separate AWS account with its own state bucket.
- Concrete values live in `backends/<env>.hcl`, passed via
  `-backend-config` at init. CI picks the right file automatically.

### 4.6 Manual / local run (recovery or investigation)

```bash
# 1. Select the environment's backend (own account + state bucket)
terraform init -backend-config="backends/<env>.hcl"

# 2. Select the matching workspace
terraform workspace select <env>    # develop | uat | prod

# 3. Fetch the environment's tfvars from S3 (never committed)
#    then plan / apply with the required vars
terraform plan \
  -var-file="environments/<env>.tfvars" \
  -var="aws_apn_id=<APN_ID>" \
  -var="strata_uat_auth_secret=<secret>" \
  -var="strata_edge_shared_secret=<secret>" \
  -var="cognito_initial_user_emails=<json-array-or-[]>" \
  -out=tfplan

terraform apply tfplan
```

### 4.7 Database migrations

Migrations run as a one-off ECS task (`migration_task`) using
`alembic upgrade head` against the `strata-core` image. Scheduled background
jobs (`strata-booking-jobs`) run on the `booking_jobs_schedule` cron.

---

## 5. Security & Compliance

### 5.1 What security scanning we do

`security_scan.yaml` runs three independent scanners on every pipeline, each in
its own job, followed by a `report` job that aggregates and gates:

| Tool | What it scans | Per-tool blocking gate |
|---|---|---|
| **Gitleaks** | Committed secrets / credentials | Any real secret blocks the merge (not severity-scored) |
| **Checkov** | IaC misconfiguration (Terraform) | `soft_fail: true`, `hard_fail_on: CRITICAL` — only a CRITICAL blocks |
| **Trivy** | IaC config scan | Blocking scan narrowed to `CRITICAL` (full-severity pass runs report-only) |

- Gitleaks runs the open-source CLI directly (the licensed action is not used)
  and produces JSON + SARIF.
- Checkov runs twice: a JSON run (feeds the gate/PR comment) and a separate
  SARIF run (feeds the HTML report). Console annotations are disabled
  (`output_format: json`) so accepted findings don't clutter the Actions UI.
- Trivy runs a CRITICAL-only blocking pass plus an all-severity report-only pass.

### 5.2 Thresholds and the aggregate risk gate

Beyond each tool's own gate, the `report` job computes a single **risk level** by
counting findings **per severity across all three tools** and comparing each
count to its own limit. These limits are configurable via repository Variables:

| Severity | Variable | Default limit | Meaning |
|---|---|---|---|
| CRITICAL | `SECURITY_THRESHOLD_CRITICAL` | `0` | Zero tolerance — one finding fails the run |
| HIGH | `SECURITY_THRESHOLD_HIGH` | `0` | Zero tolerance |
| MEDIUM | `SECURITY_THRESHOLD_MEDIUM` | `10` | Allowance before failing |
| LOW | `SECURITY_THRESHOLD_LOW` | `100` | Allowance before failing |

- The gate walks severities from CRITICAL → LOW and fails at the first severity
  whose count exceeds its limit (`Enforce risk gate` step, `exit 1`).
- This aggregate gate is **in addition to** each tool's own per-finding gate.
- Note: without a `BC_API_KEY`, Checkov leaves most severities `UNKNOWN`, which
  the aggregate gate does not count. Set `BC_API_KEY` for accurate severities.

### 5.3 Reporting

Every run produces reporting regardless of pass/fail (`if: always()`):

- **PR comment** — a single updated comment ("Infra Security Report") with a
  pass/fail banner, a per-check result table (Gitleaks / Checkov / Trivy /
  Aggregate Risk Gate), and a findings table. It is updated in place per commit,
  not re-posted.
- **Job summary** — the same tables in the GitHub Actions run summary.
- **Downloadable bundle** — artifact `security-reports-bundle` (14-day
  retention) containing each tool's raw report plus a browsable `index.html`
  dashboard (severity totals, per-check tiles, pass/blocked banner).
- Per-tool HTML reports (Gitleaks / Checkov / Trivy) are generated from SARIF and
  uploaded as separate artifacts.

### 5.4 Accepted-risk register

The following findings are **deliberately accepted** (client decision) and
suppressed so they do not block or clutter the pipeline. Review before promoting
to a stricter posture.

**Checkov** (`skip_check` in `security_scan.yaml`) — includes, among others:

| Check | Finding | Reason accepted |
|---|---|---|
| `CKV_AWS_378`, `CKV2_AWS_20` | ALB uses HTTP / no HTTPS redirect | Plain HTTP accepted until ACM cert exists per env |
| `CKV2_AWS_5` | Security group not attached | Accepted as-is |
| `CKV2_AWS_61`, `CKV_AWS_300` | S3 lifecycle / abort-uploads | Not required per client |
| `CKV_AWS_338`, `CKV_AWS_158` | Log retention / KMS on log groups | Accepted |
| `CKV_AWS_304`, `CKV2_AWS_57` | Secrets Manager rotation | Manual rotation process (see §6.1) |
| `CKV_AWS_290`, `CKV_AWS_355`, `CKV_AWS_393` | IAM constraints / OIDC claims | Reviewed and accepted |
| `CKV2_AWS_28`, `CKV2_AWS_47`, `CKV2_AWS_32`, `CKV2_AWS_42` | CloudFront/ALB WAF, headers, SSL cert | Accepted |
| `CKV2_AWS_3`, `CKV2_AWS_12`, `CKV_AWS_103`, `CKV2_AWS_62` | GuardDuty scope, default SG, TLS, S3 events | Accepted |

> Keep both `skip_check` lists in `security_scan.yaml` identical (the main run
> and the SARIF run). The full authoritative list lives in that file.

**Trivy** (`.trivyignore`):

| ID | Finding |
|---|---|
| `AWS-0054` | ALB listener uses plain HTTP |
| `AWS-0104` | Security group egress allows `0.0.0.0/0` |

---

## 6. Operational Playbooks

### 6.1 Rotate a secret

Application secrets (`strata-dev-auth-secret`, `strata-edge-shared-secret`) and
DB credentials are stored in Secrets Manager, KMS-encrypted. Automatic rotation
is intentionally off (accepted risk). To rotate:

1. Update the value in GitHub secrets (`STRATA_*`) or via `-var` for a local run.
2. Re-run the environment's pipeline `apply`.
3. Restart affected ECS services so they pick up the new value.

### 6.2 Recover a secret stuck in "pending deletion"

A destroy can leave secrets in pending-deletion, which blocks recreating them
under the same name. This is a known scenario handled by import blocks in
`main.tf`.

```bash
aws secretsmanager restore-secret --secret-id <name>
```

Then re-run `apply`. The import blocks reconcile the restored secret into state.
(Prefer `restore-secret` over force-delete — it is reversible.)

### 6.3 Recover a KMS key stuck in "pending deletion"

```bash
aws kms cancel-key-deletion --key-id <id>
aws kms enable-key --key-id <id>     # cancel leaves the key Disabled
```

Then re-run `apply` (the KMS import block adopts it).

### 6.4 Poisoned ECS task-definition revision

If a service runs with a stale/incorrect env var (e.g. carried forward by the
app deploy pipeline), force a fresh revision:

- The uat pipeline already applies temporary `-replace` flags for
  `strata-booking`, `strata-terminology`, `strata-engine-auth`. Remove once
  confirmed healthy.
- For a manual fix: `terraform apply -replace='module.ecs.aws_ecs_task_definition.services["<svc>"]'`

### 6.5 Cognito role groups missing / empty `/v1/auth/me` roles

The app expects `coach`, `member`, `admin` groups to exist. If a user has a DB
role but an empty roles list, the group is missing. These are created by the
`cognito` module; import blocks in `main.tf` reconcile pre-existing groups for
develop/uat. Re-run `apply`.

### 6.6 Provision Cognito users

- Add emails to `COGNITO_INITIAL_USERS_JSON` (JSON array). Terraform generates a
  random temporary password per user; retrieve with
  `terraform output -json cognito_initial_user_passwords` (sensitive).
- Or run the `provision_users.py` one-off task via the deploy pipeline. The
  deploy role has read-only access to the ECS log group for self-diagnosis.

### 6.7 Read service logs

```bash
aws logs tail /aws/ecs/wandahealth-<env> --follow
```

### 6.8 Scale a service

Adjust `ecs_desired_count` / `ecs_min_capacity` / `ecs_max_capacity` in the
environment tfvars and re-apply. Autoscaling is configured in `compute/ecs`.

---

## 7. Backup, DR & State Management

### RDS

- Automated backups: `rds_backup_retention_days`, window `rds_backup_window`.
- Maintenance window: `rds_maintenance_window`.
- Multi-AZ: `rds_multi_az` (recommended `true` for prod).
- Deletion protection: `rds_deletion_protection` (recommended `true` for prod).
- Restore: use AWS console/CLI point-in-time restore or restore from an
  automated snapshot into a new instance, then repoint the app secret.

### Terraform state

- State lives in a per-account S3 bucket (`backends/<env>.hcl`),
  key `savanna/terraform.tfstate`, encrypted.
- **Never** run against the wrong workspace/backend — each is a different AWS
  account. Always `init` with the right backend config and `workspace select`.
- Import blocks in `main.tf` reconcile hand-created or restored resources into
  state (Cognito groups, restored secrets, cancelled KMS key).

---

## 8. Monitoring & Alerting

- CloudWatch log groups per ECS service (`/aws/ecs/wandahealth-<env>`).
- Dashboards and alarms provisioned by `monitoring/cloudwatch`.
- GuardDuty enabled for threat detection.
- WAFv2 WebACL attached to the ALB with a configurable rate limit
  (`waf_rate_limit`).

---

## 9. Teardown

- Run the environment pipeline with `action: destroy` (dispatch).
- **Prod destroy is blocked** by the precheck job — intentional.
- Destroying leaves secrets/KMS in pending-deletion states; see §6.2 / §6.3 to
  recover before the next apply.
