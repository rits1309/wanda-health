# wanda-altcloud-infra-code 

Terraform for the AWS infrastructure that Strata.Cloud (the `wanda-altcloud`
application repo) deploys onto: networking, compute, database, storage,
security, and auth for the `develop`, `uat`, and `prod` environments.

Two reference documents accompany the code:

- **[WandaHealth-Infrastructure-Modules.md](WandaHealth-Infrastructure-Modules.md)**
  — a plain-language walkthrough of every module: what it creates, why, and
  how a request actually flows through the system end to end.
- **[Auth `/v1/auth/me` request flow](docs/auth-me-request-flow.md)** — the
  screenshot-ready evidence walkthrough for the Cognito → API Gateway → VPC
  Link → ALB → ECS → RDS path, verified against a real deployment.

## Repository layout

| Path | What it is |
|---|---|
| `main.tf` / `variables.tf` / `outputs.tf` / `locals.tf` | Root module — wires every child module together for one environment. |
| `backend.tf` / `provider.tf` / `versions.tf` | S3 backend (workspace-keyed state), AWS provider, Terraform/provider version pins. |
| `modules/` | 14 modules, one AWS surface each — `networking/` (vpc, subnets, nat-gateway, route-tables, security-groups, alb, api-gateway, internet-gateway), `security/` (kms, iam, secrets-manager), `compute/` (ecr, ecs), `database/` (rds), `storage/` (s3), `auth/` (cognito). |
| `.github/workflows/` | The CI/CD pipeline that runs this Terraform. See below. |
| `tf.ps1` | Local wrapper: pulls the target workspace's `.tfvars` from S3, runs the Terraform command, and can push edits back. |

## Environments

There are three: `develop`, `uat`, `prod` — each is its own Terraform
**workspace**, and `local.name_prefix` (`locals.tf`) derives every resource
name directly from the workspace (`wandahealth-<workspace>-<resource>`). The
workspace is never picked by hand in CI — the pipeline reads it straight off
the branch/environment that triggered the run.

**`.tfvars` files are never committed.** Each environment's real configuration
(instance sizing, CIDR blocks, feature toggles — never secrets, which are
injected separately as `-var` flags from GitHub Actions secrets) lives at
`s3://wanda-terraform-state/environments/<workspace>.tfvars` and is fetched at
pipeline runtime. `.gitignore` blocks it, and a precheck step in CI fails the
run outright if a `.tfvars` file is ever tracked, as a backstop beyond
`.gitignore` alone.

## Running it locally

```powershell
$env:AWS_PROFILE = "wanda-health"
terraform init
terraform workspace select -or-create develop
.\tf.ps1 plan      # pulls environments/develop.tfvars from S3 first
.\tf.ps1 apply
```

`tf.ps1 edit` pulls the current tfvars, opens them for editing, and pushes the
result back to S3 when you're done — that's the only supported way to change
an environment's configuration.

## The pipeline

`develop` and `uat` run unrestricted: a pull request runs
`init → fmt → validate → plan` for review, and a push (a PR landing) runs the
same chain through `apply` — no approval gate. `prod` has no push or
pull_request trigger at all; it's reachable only by manually dispatching
`terraform-prod.yaml`, gated by the `prod` GitHub Environment's required
reviewers. Every environment also supports manual `workflow_dispatch` for an
on-demand plan/apply/destroy.

Full trigger-by-trigger detail lives in `.github/workflows/` itself (each file
is commented) — there's no separate workflow README to keep in sync.

## Security scanning

Every run scans with Gitleaks (secrets), Checkov (Terraform misconfiguration),
and Trivy (config). Gitleaks also runs locally before every commit — install
the hook once per clone:

```bash
pip install pre-commit
pre-commit install
```

Gitleaks blocks on any finding. Checkov and Trivy scan and report everything,
but only a CRITICAL-severity finding fails the run — everything else still
shows up in full in the PR comment and job summary, it just doesn't block the
merge. Two specific findings (the ALB's plain-HTTP listener, a security
group's `0.0.0.0/0` egress rule) are explicitly accepted as a documented risk
in `.trivyignore` rather than silently ignored.

Every resource also requires a real value for `aws_apn_id` (the AWS Partner
Central migration tag, applied via `provider.tf`'s `default_tags`) — an
environment's `terraform plan`/`apply` refuses to run at all without it set
in that environment's tfvars.

## Data sensitivity

This infrastructure hosts Strata.Cloud, which handles PHI in production. See
`wanda-altcloud`'s `architecture.md` for the binding HIPAA-compliance
requirements the deployment must satisfy.
