# Strata.Cloud — Savanna backend platform

This repository contains **Strata.Cloud**, the backend platform of Wanda's Savanna
ecosystem: a multi-service Python monorepo (FastAPI services plus shared libraries)
backed by a single canonical PostgreSQL schema.

Two reference documents accompany the code:

- **[architecture.md](architecture.md)** — the AWS implementation brief: what to
  build on AWS to run the platform, the binding architecture principles, and the
  compliance requirements the deployment must satisfy.
- **[terminology.md](terminology.md)** — the canonical names for every component
  and programme. Please use these names in all communication and documentation.

## Repository layout

| Folder | What it is | Runs as |
|---|---|---|
| `strata-cloud/strata.core` | The canonical database project: **all** schema (SQLAlchemy models), the single Alembic migration history, fixture profiles, and the field-level data-classification catalog. Not a service — consumed as a package by every service. | Library + tooling |
| `strata-cloud/strata.engine.auth` | Authentication service: Cognito-backed `/v1/auth` (login, refresh, `/v1/auth/me`), including seamless legacy-user migration at first sign-in. | API on **:8000** |
| `strata-cloud/strata.booking` | Coaching appointment booking: availability, slot generation, booking, rescheduling, cancellation policies, reminders, no-show detection. | API on **:8010** |
| `strata-cloud/strata.connect` | Device-readings integration pipeline (SmartMeter): ingest edge, processor/worker pipeline, quarantine and operator tooling. | API on **:8004** |
| `strata-cloud/strata.terminology` | Clinical terminology (reference data): FDA NDC medications, ICD-10 procedures/diagnoses; ingest pipelines + authenticated search APIs. | API on **:8020** |
| `strata-cloud/strata.engine` | Service shell carrying the shared observability baseline. | API on **:8000** |
| `strata-cloud/strata.identity` | Identity-logic library: the shared token-verification seam, wire types, profile/role helpers. | Library |
| `strata-cloud/strata.member` | Write-seam library for member clinical data. | Library |

Every service has the same shape: `pyproject.toml`, a `.env.example`, an
[Invoke](https://www.pyinvoke.org/) `tasks.py` as the command interface, and a
full test suite under `tests/`.

## Prerequisites

- Python **3.13+**
- Docker (local PostgreSQL via the root `docker-compose.yml`, and
  [testcontainers](https://testcontainers.com/)-based test suites)
- An AWS account (only for the Cognito-backed auth paths; everything else runs
  fully locally in dev mode)

## Quickstart (local)

All schema truth lives in `strata.core` — set it up first:

```bash
cd strata-cloud

# 1. The ONE shared local Postgres (port 5432) for the whole workspace
docker compose up -d --wait db

# 2. Install strata.core and apply the canonical migrations + demo fixtures
cd strata.core
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
.venv/bin/inv migrate
.venv/bin/inv seed --profile demo    # profiles: minimal · demo · booking-acceptance · readings-demo

# 3. Run a service (booking shown; same pattern for every service)
cd ../strata.booking
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" -e ../strata.core -e ../strata.identity
cp .env.example .env
.venv/bin/inv dev                    # http://127.0.0.1:8010 — OpenAPI docs at /docs
```

Each service's `tasks.py` is the authoritative command list: `inv --list` shows
everything (dev server, migrate, seed, checks, tracing).

**Dev-mode authentication:** services verify bearer tokens through the shared
`strata.identity` seam. For local work without Cognito, set `STRATA_AUTH_MODE=dev`
and `STRATA_ENVIRONMENT=local` (dev mode deliberately refuses to start otherwise)
and mint a token with `inv token --role coach --sub c-1`. The auth service itself
(`strata.engine.auth`) requires a real Cognito user pool — see its `.env.example`
for the required settings and the deployment notes below.

## Per-folder environment matrix

There is no single monorepo environment: **each `strata.*` folder is its own
Python project with its own `.venv` and its own `.env`**, plus the one shared
Docker Postgres at the repo root. `strata-core` and `strata-identity` are *local
path dependencies* (not on PyPI), so every install that needs them must name them
explicitly — pip cannot resolve them otherwise. Set up `strata.core` first (it owns
the schema), then only the folders you need:

| Folder | venv | `.env` | Install (from inside the folder) |
|---|---|---|---|
| repo root | — | — | `docker compose up -d --wait db` (shared Postgres 16; also required by the test suites' testcontainers) |
| `strata.core` | yes | yes | `pip install -e ".[dev]"` — then `inv migrate && inv seed --profile demo` |
| `strata.engine.auth` | yes | yes — the only service needing **real Cognito values** to fully run | `pip install -e ".[dev]" -e ../strata.core -e ../strata.identity` |
| `strata.booking` | yes | yes (dev-mode auth works) | `pip install -e ".[dev]" -e ../strata.core -e ../strata.identity` |
| `strata.connect` | yes | yes (dev-mode auth works) | `pip install -e ".[dev]" -e ../strata.core -e ../strata.identity` |
| `strata.terminology` | yes | yes (dev-mode auth works) | `pip install -e ".[dev]" -e ../strata.core -e ../strata.identity` |
| `strata.engine` | yes | yes | `pip install -e ".[dev]"` (no core dependency) |
| `strata.identity` | only to run its test suite | yes | `pip install -e ".[dev]" -e ../strata.core` |
| `strata.member` | only to run its test suite | none shipped | `pip install -e ".[dev]" -e ../strata.core` |

Example — the booking API running from a fresh clone touches exactly three
places: repo root (Docker), `strata.core` (venv + migrate + seed), and
`strata.booking` (venv + `.env` + `inv dev`). The two libraries
(`strata.identity`, `strata.member`) need environments only to run their own
test suites — services consume them through the path-dependency mechanism.

## Testing

Every service runs the same gate:

```bash
.venv/bin/inv check    # lint (ruff) + typecheck (mypy) + security (bandit) + tests
```

Test suites that touch the database spin up their own PostgreSQL 16 via
testcontainers — Docker must be running; no shared state with your dev database.
The GitHub Actions workflows under [.github/workflows/](.github/workflows/)
run service smoke tests plus SecOps scanning, image build/sign/push, and ECS
deployment. The per-service quality and pytest gates remain available through
`inv check` locally and are currently commented out in the reusable CI workflow;
see [.github/workflows/README.md](.github/workflows/README.md) for the active
pipeline and its known gaps.

On Windows, the `.venv/bin/...` paths above are `.venv/Scripts/...` instead, and
`pytest` should run with `PYTHONUTF8=1` set — otherwise Python's default `cp1252`
file encoding mangles non-ASCII characters (e.g. em dashes) in generated docs like
`DATA_CLASSIFICATION_REGISTER.md`, causing `test_classification_register.py` to
fail on a spurious mismatch. CI's Linux runners are unaffected.

## Database changes

`strata.core` owns all schema. The only way to change schema: edit the models in
`strata.core/strata_core/domains/`, generate a migration with
`inv migration --name "..."` (it must round-trip — the migration tests replay
upgrade → downgrade → upgrade and run a drift check), classify any new column in
the data-classification catalog (`classifications.py` — CI-enforced), and update
the fixture profiles the change touches. Services never define models or
migrations of their own, and `create_all` is prohibited — migrations are the
single authority.

## Deploying to AWS

Deployment itself is automated — see
[.github/workflows/README.md](.github/workflows/README.md) for how the CI/CD
pipeline builds, scans, signs, and deploys each service to ECS Fargate, and
what AWS/GitHub configuration it expects to already exist. The rest of this
section is about the target architecture those deploys land on.

[architecture.md](architecture.md) is the authoritative build specification for
the target AWS architecture — in brief: containerised FastAPI services on
**ECS Fargate**, **RDS PostgreSQL 16** in private subnets (the local compose file
pins Postgres 16 to match), a single **Cognito user pool** with per-surface app
clients, **API Gateway** + **WAF** at the edge, KMS encryption,
CloudWatch/X-Ray/GuardDuty observability, and Secrets Manager for all
credentials. Its Section 6 states the HIPAA-compliance requirements the
deployment must satisfy.

Environment-specific values (account, region, VPC/subnets, RDS endpoint, Cognito
pool and client IDs) are supplied via each service's environment — the
`.env.example` files enumerate every setting with placeholders. Copy them, fill in
values for your target account, and never commit real identifiers or credentials.

Notes:

- **Cognito**: create a user pool with app clients per surface; the pool's password
  policy must match the mirror settings in `strata.engine.auth`'s configuration
  (they default to Cognito's defaults). There is deliberately no self-signup.
- **Database**: RDS PostgreSQL 16. Apply migrations with `inv migrate` from
  `strata.core` pointed at the target `STRATA_DATABASE_URL`.
- **Observability**: services emit OTLP traces (X-Ray backend in AWS; a local
  Jaeger is available via `inv trace-up`) and structured JSON logs
  (`STRATA_LOG_FORMAT=json` for CloudWatch).

## Data sensitivity

This platform handles PHI in production. The field-level sensitivity of every
database column is recorded in the data-classification catalog
(`strata-cloud/strata.core/strata_core/CLASSIFICATIONS.md`, with the generated
per-field register alongside it) — consult it before handling, logging, or
exporting any field's data. All fixture/seed data in this repository is synthetic.
