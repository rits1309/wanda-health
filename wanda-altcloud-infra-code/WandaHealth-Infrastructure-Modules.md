# WandaHealth Infrastructure - Terraform Module Documentation

| Document Status | Active |
|---|---|
| Version | 1.0 |
| Date | August 24, 2026 |
| Project | WandaHealth - Strata Cloud Platform |
| AWS Account | 840080485141 |
| Region |

---

## 1. Architecture Overview

The WandaHealth platform runs on AWS using a fully automated infrastructure defined as code (Terraform). The infrastructure is designed to be secure, scalable, and isolated across environments (develop, uat, prod) using Terraform workspaces.

Every resource is named using the convention `wandahealth-<environment>-<resource>`, making it easy to identify which environment a resource belongs to when viewing the AWS console.

### How a User Request Flows Through the System

When a user (via the Summit web app or Vista mobile app) makes an API call, the request travels through the following path:

```
User (Browser/App)
  → API Gateway (validates identity via JWT token)
    → VPC Link (private tunnel into the network)
      → ALB (routes request to the correct microservice based on URL path)
        → ECS Fargate (runs the application container)
          → RDS PostgreSQL (reads/writes patient and booking data)
```

If the user doesn't have a valid authentication token, the request is rejected at the API Gateway level and never reaches the backend services.

### Module Categories

The infrastructure is organized into logical categories:

| Category | What It Handles |
|---|---|
| Auth | User authentication and identity (login, tokens) |
| Networking | Network layout, traffic routing, and access control |
| Security | Encryption, permissions, secrets, and threat detection |
| Compute | Application containers and their registries |
| Database | Persistent data storage |
| Storage | File and log storage |

---

## 2. Auth Module

### 2.1 Cognito (`modules/auth/cognito/`)

Amazon Cognito is the identity service that manages user accounts and authentication. When a user logs in through the app, Cognito verifies their credentials and issues a signed token (JWT) that proves who they are. This token is then sent with every API request.

| Resource | Description |
|---|---|
| User Pool | The directory of all user accounts. Handles password policies, account verification, and token issuance. |
| App Client | The configuration that allows the Summit/Vista apps to communicate with the User Pool. |

**Why it matters:** No user can access any API endpoint without first authenticating through Cognito. Self-signup is disabled, meaning accounts can only be created by authorized administrators.

---

## 3. Networking Modules

Networking modules define how traffic moves through the system, which components can talk to each other, and how the platform is isolated from the public internet.

### 3.1 VPC (`modules/networking/vpc/`)

A Virtual Private Cloud (VPC) is an isolated private network within AWS. Think of it as the building that contains all the infrastructure. Nothing outside this network can reach internal resources unless explicitly allowed.

| Resource | Description |
|---|---|
| VPC | The isolated network (CIDR: 10.0.0.0/16 - supports ~65,000 internal IP addresses) |
| VPC Flow Logs | Records all network traffic entering and leaving the VPC for security auditing |

---

### 3.2 Internet Gateway (`modules/networking/internet-gateway/`)

The Internet Gateway is the single controlled doorway between the VPC and the public internet. Only resources in public subnets can use it. Private and database subnets have no direct internet access.

---

### 3.3 Subnets (`modules/networking/subnets/`)

Subnets divide the VPC into isolated zones with different access levels. This is a critical security measure that ensures sensitive resources (like the database) are never directly reachable from the internet.

| Subnet Type | Purpose | Internet Access |
|---|---|---|
| Public Subnets | Host the load balancer and NAT gateways | Yes (inbound and outbound) |
| Private Subnets | Run application containers (ECS) | Outbound only (via NAT) |
| Database Subnets | Host RDS database | None (completely isolated) |

All subnets are deployed across 2 availability zones (us-east-1a, us-east-1b) for high availability. If one data center goes down, the other continues to serve traffic.

---

### 3.4 NAT Gateway (`modules/networking/nat-gateway/`)

NAT (Network Address Translation) Gateways allow resources in private subnets to reach the internet (for updates, API calls to third-party services, etc.) without being directly reachable from the internet. Think of it as a one-way door: traffic can go out, but nothing can come in uninvited.

One NAT Gateway is deployed per availability zone to ensure outbound connectivity survives a single-AZ failure.

---

### 3.5 Route Tables (`modules/networking/route-tables/`)

Route tables are the traffic rules that tell each subnet where to send network packets. They ensure:

- Public subnets send internet-bound traffic to the Internet Gateway
- Private subnets send internet-bound traffic to the NAT Gateway (outbound only)
- Database subnets route through NAT Gateway for patching/updates only

---

### 3.6 ALB (`modules/networking/alb/`)

The Application Load Balancer (ALB) distributes incoming API requests to the correct backend microservice based on the URL path. It acts as a smart traffic director.

| Priority | URL Path | Routes To | Port |
|---|---|---|---|
| 10 | /v1/auth/* | strata-engine-auth (login, token refresh) | 8000 |
| 20 | /v1/booking/* | strata-booking (appointments) | 8010 |
| 30 | /v1/connect/*, /v1/readings/* | strata-connect (device data) | 8004 |
| 40 | /v1/terminology/* | strata-terminology (clinical codes) | 8020 |
| 50 (default) | /v1/* | strata-engine (catch-all) | 8000 |

The ALB also performs health checks on each service every 30 seconds. If a service becomes unhealthy, traffic is automatically routed away from it.

---

### 3.7 API Gateway (`modules/networking/api-gateway/`)

Amazon API Gateway is the public-facing entry point for all API traffic. It sits in front of the ALB and provides authentication enforcement at the network edge, before requests reach any backend service.

**Key capabilities:**
- Validates every JWT token against Cognito before allowing the request through
- Rejects unauthenticated requests with a 401 response (no backend resources consumed)
- Routes traffic through a VPC Link (a private, encrypted tunnel) to the internal ALB
- Logs all requests for auditing and debugging
- Supports CORS for browser-based apps

**Route Protection:**

| Route | Authentication Required? | Reason |
|---|---|---|
| GET /health | No | Load balancer and monitoring health checks |
| POST /v1/auth/{proxy+} | No | Login endpoints must be accessible without a token |
| ANY /v1/{proxy+} | Yes (JWT) | All other API operations require authentication |

---

### 3.8 Security Groups (`modules/networking/security-groups/`)

Security groups act as virtual firewalls around each resource type. They control exactly which traffic can reach each component and from where.

| Security Group | What Can Reach It | Why |
|---|---|---|
| ALB SG | Anyone on port 80/443 | The load balancer is the public entry point |
| ECS SG | Only ALB SG on ports 8000-8020 | Only the load balancer should talk to app containers |
| RDS SG | Only ECS SG and Lambda SG on port 5432 | Only application code should access the database |
| Lambda SG | Nothing inbound (egress only) | Lambda functions only make outbound calls |

This layered approach means even if an attacker compromises one layer, they cannot move laterally to other components.

---

## 4. Security Modules

Security modules handle encryption, access control, secret management, and threat detection.

### 4.1 KMS (`modules/security/kms/`)

AWS Key Management Service (KMS) provides a centralized encryption key used to encrypt all sensitive data across the platform. A single key encrypts:

- Database storage (RDS)
- Container logs (CloudWatch)
- File storage (S3)
- Secrets (Secrets Manager)
- Container images (ECR)

The key has automatic annual rotation enabled and a 30-day deletion window to prevent accidental data loss.

---

### 4.2 IAM (`modules/security/iam/`)

IAM (Identity and Access Management) roles define what each service is allowed to do within AWS. Every component runs with the minimum permissions required (principle of least privilege).

| Role | What It Can Do |
|---|---|
| ECS Execution Role | Pull container images, read secrets, write logs |
| ECS Task Role | Access S3, SQS, and decrypt with KMS (application-level actions) |
| Lambda Execution Role | Run within the VPC and write logs |
| Config Role | AWS Config compliance checking |

No long-lived access keys are used anywhere. All permissions are granted via IAM roles that provide temporary, automatically-rotated credentials.

---

### 4.3 Secrets Manager (`modules/security/secrets-manager/`)

AWS Secrets Manager securely stores sensitive values like database passwords and API keys. Secrets are encrypted with KMS and injected into running containers at runtime through IAM role-based access.

| Secret | Content |
|---|---|
| DB Credentials | PostgreSQL connection URL (asyncpg format), username, password |
| Edge Shared Secret | Authentication between services and edge devices |
| Dev Auth Secret | Development authentication configuration |

**Why it matters:** No passwords or API keys are ever stored in code, environment files, or container images. They exist only in Secrets Manager and are fetched at runtime.

---

### 4.4 GuardDuty (`modules/security/guardduty/`) - PLANNED

Amazon GuardDuty is a threat detection service that continuously monitors for malicious activity and unauthorized behavior. When enabled, it will:

- Analyze VPC flow logs, CloudTrail events, and DNS queries for threats
- Alert on suspicious login patterns, cryptocurrency mining, and data exfiltration attempts
- Send high-severity findings to SNS for immediate notification

---

### 4.5 WAF (`modules/security/waf/`) - PLANNED

AWS Web Application Firewall (WAF) protects against common web exploits. When enabled, it will:

- Block SQL injection attempts
- Block known-bad request patterns
- Rate-limit clients to 2000 requests per 5 minutes (prevents DDoS and scraping)
- Log all blocked requests for review

---

## 5. Compute Modules

Compute modules handle the application containers that run the business logic.

### 5.1 ECR (`modules/compute/ecr/`)

Amazon Elastic Container Registry (ECR) stores the Docker container images for each microservice. Think of it as a private, secure Docker Hub.

| Repository | Contains |
|---|---|
| strata-engine-auth | Authentication service image |
| strata-booking | Booking and appointment service image |
| strata-connect | Device readings integration image |
| strata-terminology | Clinical reference data service image |
| strata-engine | Core platform service image |
| strata-core | Database migration runner image |

**Security features:**
- All images are encrypted with KMS
- Images are scanned for vulnerabilities on every push
- Tag immutability prevents overwriting production images
- Lifecycle policy automatically cleans up old/untagged images

---

### 5.2 ECS (`modules/compute/ecs/`)

Amazon ECS (Elastic Container Service) with Fargate runs the application containers without managing servers. Fargate automatically provisions the right amount of compute resources for each container.

**Cluster:** One shared cluster hosts all services (`wandahealth-<env>-cluster`)

**Long-Running Services (5):**

These are the always-running microservices that handle API requests:

| Service | What It Does | Port |
|---|---|---|
| strata-engine-auth | Handles login, token refresh, user identity | 8000 |
| strata-booking | Manages coaching appointments and availability | 8010 |
| strata-connect | Processes device readings (glucose, BP, etc.) | 8004 |
| strata-terminology | Searches clinical codes (ICD-10, NDC) | 8020 |
| strata-engine | Core platform with shared observability | 8000 |

Each service has:
- Automatic scaling based on CPU and memory usage (targets 70% utilization)
- Health checks every 30 seconds
- Deployment circuit breaker (automatically rolls back failed deployments)
- Structured JSON logging to CloudWatch

**Scheduled Task (1):**

| Task | Runs Every | Purpose |
|---|---|---|
| strata-booking-jobs | 15 minutes | Tops up available appointment slots and sends booking reminders |

This runs as a short-lived container triggered by EventBridge (AWS's built-in scheduler).

**Migration Task (1):**

| Task | When It Runs | Purpose |
|---|---|---|
| wandahealth-<env>-migration | At deploy time (manually triggered) | Applies database schema changes from strata-core |

Migrations are run as a one-off task before deploying new service versions. They are never run automatically to prevent accidental schema changes.

---

## 6. Database Module

### 6.1 RDS (`modules/database/rds/`)

Amazon RDS (Relational Database Service) hosts the PostgreSQL database that stores all application data. A single shared database is used by all microservices, with schema managed by the strata-core migrations.

| Property | Value |
|---|---|
| Engine | PostgreSQL 16 |
| Database Name | `strata` |
| Instance (develop) | db.t3.micro |
| Storage | 20 GB (auto-scales to 40 GB) |
| Encryption | KMS at rest, SSL in transit |
| Backups | Daily automated (3-day retention in develop) |
| Monitoring | Enhanced monitoring every 60 seconds |

**Security features:**
- Located in isolated database subnets (no internet access)
- Accessible only from ECS and Lambda security groups
- Password stored in Secrets Manager (never in code)
- SSL/TLS enforced for all connections
- Performance Insights enabled for query analysis

**Connection method:** Services connect using async PostgreSQL (`asyncpg`) for high-performance, non-blocking database access:
```
postgresql+asyncpg://strata:<password>@<endpoint>:5432/strata
```

---

## 7. Storage Module

### 7.1 S3 (`modules/storage/s3/`)

Amazon S3 (Simple Storage Service) provides durable file storage for application assets and operational logs.

| Bucket | Purpose | Example Contents |
|---|---|---|
| Assets Bucket | Application files and uploads | User documents, report exports |
| Logs Bucket | Operational audit logs | CloudTrail events, AWS Config snapshots |

**Security and lifecycle:**
- All files encrypted with KMS
- Public access completely blocked (no accidental data exposure)
- Versioning enabled (can recover deleted or overwritten files)
- Automatic cost optimization: files move to cheaper storage tiers over time
  - After 90 days → Infrequent Access (30% cheaper)
  - After 365 days → Glacier (90% cheaper)

---

## 8. Environment Configuration

Each environment (develop, uat, prod) has its own `.tfvars` file that defines all configuration values. No values are hardcoded in the infrastructure code. This means the same code deploys consistently across environments with only the configuration differing.

### Dev Environment Settings

| Category | Setting | Value |
|---|---|---|
| Compute | ECS CPU | 256 units (0.25 vCPU) |
| Compute | ECS Memory | 512 MB |
| Compute | Tasks per service | 1 |
| Compute | Auto-scaling range | 1-2 tasks |
| Database | Instance size | db.t3.micro (small, cost-efficient) |
| Database | Multi-AZ | Disabled (single zone for develop) |
| Database | Backup retention | 3 days |
| Observability | Log retention | 14 days |
| Security | Deletion protection | Disabled (allows teardown in develop) |

Production will use larger instances, multi-AZ for high availability, longer backup retention, and deletion protection enabled.

---

## 9. Deployment Workflow

Deploying changes follows this sequence:

```
Step 1: Select the target environment
  → terraform workspace select develop

Step 2: Preview changes (no resources modified)
  → terraform plan -var-file="environments\develop.tfvars"

Step 3: Apply changes (creates/updates AWS resources)
  → terraform apply -var-file="environments\develop.tfvars"

Step 4: Run database migrations (if schema changed)
  → aws ecs run-task --task-definition wandahealth-develop-migration ...

Step 5: Deploy new application code
  → aws ecs update-service --force-new-deployment ...
```

---

## 10. Verified End-to-End Flow

The following test was successfully executed, confirming the entire infrastructure chain works:

| Step | What Happened | Result |
|---|---|---|
| 1. Get token | Called Cognito `initiate-auth` with test credentials | Received valid JWT access token |
| 2. Call API (no auth) | `curl <API_URL>/v1/auth/me` without token | 401 Unauthorized (rejected at API Gateway) |
| 3. Call API (with auth) | `curl -H "Authorization: Bearer <token>" <API_URL>/v1/auth/me` | `{"detail":"No profile for this subject"}` |

The "No profile" response confirms:
- Cognito issued a valid token
- API Gateway validated the token and allowed the request through
- VPC Link successfully tunneled to the internal ALB
- ALB routed to the correct ECS service (strata-engine-auth on port 8000)
- The service validated the JWT again (defense-in-depth)
- The service connected to RDS and queried the database
- The database responded (user has no profile row yet - expected for a new test user)

---

## 11. Security Summary

| Layer | Protection |
|---|---|
| Edge | API Gateway rejects unauthenticated requests before they reach backend |
| Network | Private subnets, security groups restrict all lateral movement |
| Identity | Cognito + JWT with double-validation (gateway + application) |
| Encryption | KMS encrypts all data at rest; TLS encrypts all data in transit |
| Secrets | No credentials in code; Secrets Manager with IAM-based access only |
| Audit | VPC flow logs, CloudTrail, CloudWatch access logs on all components |
| Availability | Multi-AZ subnets, auto-scaling, deployment circuit breakers |
