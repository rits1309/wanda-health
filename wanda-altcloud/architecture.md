Savanna: AWS Implementation Brief | v1.0 | Draft | Partner-Shared

# Savanna: AWS Implementation Brief

*Prepared for the altcloud engagement*

| **Document Status** | **Draft** |
| --- | --- |
| **Version** | 1.0 |
| **Date** | 07-08-2026 |
| **Owner** | CEngO |
| **Classification** | Partner-Shared |

> This document is the partner-facing implementation brief, derived from Wanda's
> internal architecture reference. It states **what needs to be built on AWS** to
> run the Savanna backend platform. Canonical component names are defined in
> [terminology.md](terminology.md); local development and testing are covered by
> the repository [README](README.md).

## 1. What you are deploying

**Savanna** is Wanda's health-coaching technology ecosystem. This engagement covers
its AWS platform foundation, **Strata.Cloud** — the services in the `strata-cloud/`
monorepo — plus the hosting for **Summit**, the professional web portal that
consumes them. The platform handles **PHI** and must be deployed to HIPAA-grade
standards (Section 6).

| Component | What it is | Runs as |
|---|---|---|
| Strata.Engine.Auth | Authentication service (Cognito-backed `/v1/auth`: login, refresh, me) | Fargate service, container port 8000 |
| Strata.Booking | Coaching appointment booking APIs + background jobs | Fargate service, port 8010 |
| Strata.Connect | Device-readings integration pipeline (SmartMeter, cloud-to-cloud) | Fargate service (port 8004) + serverless ingest (§3.5) |
| Strata.Terminology | Clinical reference-data search APIs (FDA NDC, ICD-10) + ingest pipelines | Fargate service, port 8020 |
| Strata.Engine | Service shell carrying the shared observability baseline | Fargate service, port 8000 |
| Strata.Core | Canonical database schema, migrations, fixtures — a package, not a service | Deploy-time migration step (§4.2) |
| Summit | React single-page app for coaches, Wanda staff, and PBM operators | S3 + CloudFront (§3.6) |

## 2. Architecture principles that bind the deployment

- **API-first**: clients reach the platform only through the service APIs. All
  APIs are versioned under `/vN` (currently `/v1`); `/health` stays unversioned
  and open for load-balancer checks.
- **One operational database**: every service reads/writes the single Strata.Core
  PostgreSQL database. Schema and migrations ship only in the `strata.core`
  package — services carry none of their own.
- **No client talks to Cognito directly**: all authentication flows through
  Strata.Engine.Auth's endpoints.
- **Everything private by default**: only API ingress and the Summit CDN are
  publicly reachable.

## 3. What to build on AWS

### 3.1 Networking

- One VPC per environment with private subnets for all compute and data;
  public subnets only for managed ingress (NAT as needed for egress).
- Security groups restricted to the minimum flows (services → RDS 5432;
  ingress → services).
- VPC flow logs enabled, feeding GuardDuty and CloudWatch.

### 3.2 Compute — ECS Fargate

- One Fargate service per backend service in the table above, deployed from
  container images built out of the monorepo (each service directory is
  self-contained Python 3.13 / FastAPI; no Dockerfiles ship in this drop —
  image definitions are part of the deployment work).
- Independent scaling per service; ALB (or API Gateway) target per service
  routing on path/host, health-checked on `/health`.
- Strata.Booking additionally needs a scheduled runner for its background jobs
  (slot top-up + reminders): invoke its jobs entrypoint on a schedule
  (EventBridge → ECS task is the expected shape).

### 3.3 Data — RDS PostgreSQL

- **RDS for PostgreSQL 16** (the repo's local compose pins 16 to match), in
  private subnets: Multi-AZ, automated backups + point-in-time recovery,
  KMS encryption at rest, managed patching.
- One database (`strata`) shared by all services; credentials in Secrets
  Manager, injected at runtime (§3.7).
- Migrations are a **deploy step**, not an app-startup step (§4.2).

### 3.4 Identity — Amazon Cognito

- **One user pool** for the whole platform, with **one app client per surface**
  (Summit today; Vista later). No client secret on the Summit client.
- **Self-signup disabled** — accounts originate only from controlled paths
  (the in-service legacy migration, admin onboarding, seeds in dev).
- Pool password policy must match the mirror settings in Strata.Engine.Auth's
  configuration (both default to Cognito's defaults — change together).
- Strata.Engine.Auth needs a scoped IAM role for exactly its admin operations
  (account creation during the legacy-migration login and dev seeding); the
  runtime login path uses Cognito's unauthenticated IdP API and needs no AWS
  credentials.
- Machine-to-machine calls between services use the Cognito client-credentials
  flow.

### 3.5 Integration pipeline — Strata.Connect ingest

The inbound readings path is serverless in front of the Fargate service:

**API Gateway → Lambda → SQS → Processor/Worker (in the Connect service) → RDS**

- API Gateway receives provider webhooks (SmartMeter) and routes to Lambda.
- Lambda writes the **complete raw payload to S3** (audit + replay), then
  enqueues a lightweight reference message with a correlation ID.
- SQS main queue **plus a DLQ per queue**, with CloudWatch alarms on DLQ depth.
  Redelivery/backoff configuration is specified in the service's settings.
- The S3 raw store is private, KMS-encrypted, lifecycle-managed; replay
  re-queues the stored payload under its original correlation ID.
- Evaluate Lambda provisioned concurrency once real SmartMeter batch-sync
  traffic patterns are known.

### 3.6 Web frontend — Summit

- React SPA served from a private S3 bucket via CloudFront; ACM-managed TLS.
- WAF attached at the CloudFront distribution (and at API ingress, §3.8).
- The Summit build consumes the service APIs cross-origin: service CORS
  origins are environment configuration.

### 3.7 Secrets and configuration

- **AWS Secrets Manager** for all credentials: database, Cognito client
  settings, third-party API keys. Injected into Fargate tasks via IAM
  task-role access — never environment files, never long-lived keys.
- Every service's configuration surface is enumerated in its `.env.example`
  (all keys use the `STRATA_` prefix). Environment-specific values — account,
  region, VPC, RDS endpoint, pool/client IDs — are deployment configuration.
- Production services run with `STRATA_AUTH_MODE=cognito`. Dev-token mode
  refuses to start unless the environment is explicitly local — do not carry
  dev settings into deployed environments.

### 3.8 Edge protection

- **AWS WAF** in front of all public ingress (API Gateway/ALB and CloudFront):
  rate limiting, AWS managed OWASP rule groups, IP reputation, bot control
  (scraping of member health data is a specific HIPAA concern).
- **ACM** for TLS on every public endpoint; TLS everywhere in transit.

### 3.9 Observability, audit, and threat detection

- **CloudWatch**: services emit structured JSON logs (`STRATA_LOG_FORMAT=json`)
  with correlation IDs on every request/message. Alarms on error rates,
  authentication-failure spikes, and DLQ depth.
- **X-Ray via OpenTelemetry**: services export OTLP traces — run an OTLP
  collector/endpoint for them (locally the repo uses Jaeger; the AWS target is
  the X-Ray backend). Correlation IDs propagate across API, queue, and DB hops.
- **GuardDuty** on the account; **AWS Config** for drift from the security
  baseline; **CloudTrail** to a dedicated KMS-encrypted, integrity-validated
  bucket. These produce the continuous audit evidence the compliance posture
  requires.

### 3.10 Messaging and communications

- **SES** (member email) and **SNS Mobile Push** (APNs/FCM, when Vista lands)
  — provisioned with DKIM/SPF and bounce/complaint handling for SES. Push and
  email payloads never carry PHI (non-clinical prompts only).
- **Twilio** provides voice/SMS via the services as control plane; its
  credentials live in Secrets Manager. Call recordings, when enabled, land in
  a private KMS-encrypted S3 bucket — media never rides through third-party
  storage as the durable copy.

## 4. Deployment responsibilities

### 4.1 CI/CD

The repo ships per-service GitHub Actions for lint/type/test gates. Image
build, push, and deployment pipelines to the target account are part of the
implementation work, as is the platform IaC.

### 4.2 Database migrations

Run `inv migrate` from `strata.core` against the target `STRATA_DATABASE_URL`
as a gated deploy step before rolling services. One linear migration history;
never create schema any other way. Seeding via fixture profiles is for
non-production environments only.

### 4.3 Environments

Separate AWS environments (at minimum staging and production) with no shared
data stores; production data never leaves production.

## 5. Non-functional requirements

To be agreed with Wanda before production cutover, and treated as build inputs
rather than afterthoughts: availability targets, RPO/RTO and backup/restore
testing cadence, service-level objectives for the key workflows (login, member
lookup, readings ingestion), alerting thresholds and runbooks, and rollback
procedure for deployments.

## 6. Compliance requirements

The platform processes PHI. The deployment must satisfy: encryption at rest
(KMS) and in transit (TLS) everywhere; least-privilege IAM; private-by-default
networking; complete audit trails (CloudTrail, access logging, key-usage
logging); environment separation; and evidence generation suitable for HIPAA
and ISO 27001 audit. Field-level data sensitivity is catalogued in the repo
(`strata-cloud/strata.core/strata_core/CLASSIFICATIONS.md`) — consult it before
introducing any new logging, export, or data-movement path.

---

Prepared for altcloud | Classification: Partner-Shared | Owner: CEngO
