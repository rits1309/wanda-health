# =============================================================================
# WandaHealth Infrastructure - Strata Cloud Platform
# Workspaces: develop | uat | prod
# =============================================================================

locals {
  # Real top-level route prefixes each service's own FastAPI app registers
  # directly under /v1 (confirmed by reading every api/*.py router's own
  # `prefix=`) -- none of these apps follow a "/v1/<service-name>/*"
  # convention, which is what the ALB rules originally (wrongly) assumed.
  #
  # Both the bare exact path and the wildcard form are generated for each
  # prefix below: several of these routers register a route at the bare
  # prefix itself (e.g. GET /v1/members, POST /v1/readings), which a
  # "/v1/<prefix>/*" pattern alone does NOT match -- AWS's `*` requires a
  # literal trailing "/" plus at least the empty string after it, not the
  # bare path with nothing after. Confirmed live, in two stages: first
  # "/v1/coaches/*/calendar" (which has a real suffix) started working
  # immediately, while "/v1/members" and "/v1/readings" (bare-only routes)
  # still 404'd with only the wildcard form present -- fixed by adding the
  # exact-path form alongside every wildcard, not just the ones known to
  # need it today, so a future bare route added to any of these services
  # doesn't silently repeat this exact bug.
  booking_prefixes = [
    "availability-patterns", "unavailability-blocks", "unavailability-patterns",
    "coaches", "members", "slots", "appointments", "cancellation-policies",
    "alternatives", "reschedule", "admin",
  ]
  connect_prefixes     = ["readings", "device-registrations"]
  terminology_prefixes = ["medications", "procedures", "diagnoses"]

  # Service definitions for ALB routing
  alb_services = {
    strata-engine-auth = {
      container_port    = 8000
      health_check_path = "/health"
      path_pattern      = ["/v1/auth/*"]
      priority          = 10
    }
    strata-booking = {
      container_port    = 8010
      health_check_path = "/health"
      # 11 prefixes x 2 forms (bare + wildcard) = 22 patterns, well over
      # AWS's 5-per-rule limit, so the ALB module splits this into
      # multiple rules automatically.
      path_pattern = flatten([for p in local.booking_prefixes : ["/v1/${p}", "/v1/${p}/*"]])
      priority     = 20
    }
    strata-connect = {
      container_port    = 8004
      health_check_path = "/health"
      path_pattern      = flatten([for p in local.connect_prefixes : ["/v1/${p}", "/v1/${p}/*"]])
      priority          = 30
    }
    strata-terminology = {
      container_port    = 8020
      health_check_path = "/health"
      path_pattern      = flatten([for p in local.terminology_prefixes : ["/v1/${p}", "/v1/${p}/*"]])
      priority          = 40
    }
    strata-engine = {
      container_port    = 8000
      health_check_path = "/health"
      path_pattern      = ["/v1/*"]
      priority          = 50
    }
  }

  # ECS service definitions (wired with target groups and ECR URLs)
  ecs_services = {
    for name, svc in local.alb_services : name => {
      container_port     = svc.container_port
      ecr_repository_url = module.ecr.repository_urls[name]
      target_group_arn   = module.alb.target_group_arns[name]
      task_cpu           = var.ecs_task_cpu
      task_memory        = var.ecs_task_memory
      desired_count      = var.ecs_desired_count
      min_capacity       = var.ecs_min_capacity
      max_capacity       = var.ecs_max_capacity
      health_check_path  = svc.health_check_path
    }
  }
}

# ── NETWORKING ────────────────────────────────────────────────────────────────
module "vpc" {
  source             = "./modules/networking/vpc"
  name_prefix        = local.name_prefix
  vpc_cidr           = var.vpc_cidr
  log_retention_days = var.log_retention_days
  tags               = local.common_tags
}

module "internet_gateway" {
  source      = "./modules/networking/internet-gateway"
  name_prefix = local.name_prefix
  vpc_id      = module.vpc.vpc_id
  tags        = local.common_tags
}

module "subnets" {
  source                = "./modules/networking/subnets"
  name_prefix           = local.name_prefix
  vpc_id                = module.vpc.vpc_id
  availability_zones    = var.availability_zones
  public_subnet_cidrs   = var.public_subnet_cidrs
  private_subnet_cidrs  = var.private_subnet_cidrs
  database_subnet_cidrs = var.database_subnet_cidrs
  tags                  = local.common_tags
}

module "nat_gateway" {
  source              = "./modules/networking/nat-gateway"
  name_prefix         = local.name_prefix
  availability_zones  = var.availability_zones
  public_subnet_ids   = module.subnets.public_subnet_ids
  internet_gateway_id = module.internet_gateway.internet_gateway_id
  tags                = local.common_tags
}

module "route_tables" {
  source              = "./modules/networking/route-tables"
  name_prefix         = local.name_prefix
  vpc_id              = module.vpc.vpc_id
  availability_zones  = var.availability_zones
  internet_gateway_id = module.internet_gateway.internet_gateway_id
  nat_gateway_ids     = module.nat_gateway.nat_gateway_ids
  public_subnet_ids   = module.subnets.public_subnet_ids
  private_subnet_ids  = module.subnets.private_subnet_ids
  database_subnet_ids = module.subnets.database_subnet_ids
  tags                = local.common_tags
}

module "alb" {
  source              = "./modules/networking/alb"
  name_prefix         = local.name_prefix
  vpc_id              = module.vpc.vpc_id
  public_subnet_ids   = module.subnets.public_subnet_ids
  security_group_id   = module.security_groups.alb_sg_id
  services            = local.alb_services
  default_service     = "strata-engine"
  acm_certificate_arn = var.acm_certificate_arn
  tags                = local.common_tags
}

# ── SECURITY ──────────────────────────────────────────────────────────────────
module "kms" {
  source                                   = "./modules/security/kms"
  name_prefix                              = local.name_prefix
  tags                                     = local.common_tags
  cross_account_ecr_decrypt_principal_arns = local.promotion_pull_principals
}

module "iam" {
  source                  = "./modules/security/iam"
  name_prefix             = local.name_prefix
  kms_key_arn             = module.kms.key_arn
  tags                    = local.common_tags
  environment             = terraform.workspace
  frontend_bucket_arn     = module.frontend.bucket_arn
  frontend_cloudfront_arn = module.frontend.cloudfront_arn
  cognito_user_pool_arn   = module.cognito.user_pool_arn

  promotion_source_account_id  = local.promotion_source_account
  promotion_source_repo_prefix = local.promotion_source_prefix
}

# NOTE: github-actions-infra-<env> is no longer managed by Terraform -- it's
# a manual bootstrap prerequisite created per account (see
# modules/security/iam/main.tf). No import block needed; Terraform doesn't
# know about it at all now, so there's nothing to adopt or collide with.

# coach/member/admin already exist in develop (created by hand, before this
# module existed). This import used to also list "uat", on the claim that
# an incident fix had created the same groups by hand there too -- checked
# directly against the real uat AWS account (Cognito, ECS, RDS all empty;
# only the standard Control Tower baseline VPC exists) and that claim
# doesn't hold: uat has no pre-existing Cognito pool at all. Left scoped to
# uat, the for_each still evaluates to a non-empty map, and its id then
# depends on module.cognito.user_pool_id -- a pool that doesn't exist yet
# on a from-scratch apply -- which Terraform can't resolve at plan time
# ("Invalid import id argument"), confirmed by a real plan run. develop-only
# until uat genuinely has a pre-existing pool to reconcile with; a fresh
# environment (uat today, prod always) creates these groups new, no import
# needed.
import {
  for_each = terraform.workspace == "develop" ? { coach = "coach" } : {}
  to       = module.cognito.aws_cognito_user_group.coach
  id       = "${module.cognito.user_pool_id}/${each.value}"
}

import {
  for_each = terraform.workspace == "develop" ? { member = "member" } : {}
  to       = module.cognito.aws_cognito_user_group.member
  id       = "${module.cognito.user_pool_id}/${each.value}"
}

import {
  for_each = terraform.workspace == "develop" ? { admin = "admin" } : {}
  to       = module.cognito.aws_cognito_user_group.admin
  id       = "${module.cognito.user_pool_id}/${each.value}"
}

# A real destroy has, twice now (uat, then prod), left these three secrets
# in Secrets Manager's pending-deletion state, which blocks creating a new
# secret under the same name ("You can't create this secret because a
# secret with this name is already scheduled for deletion" -- confirmed by
# a real apply failure both times). Restored (not force-deleted -- a
# reversible, non-destructive action, unlike skipping the recovery window)
# via `aws secretsmanager restore-secret`, which brings them back active
# but leaves Terraform state not knowing about them -- so the next apply
# would otherwise try to create them again and hit "already exists".
#
# One import block per resource address, not one per environment: import
# blocks are validated by their static `to` target across the whole
# config, not per-workspace, so two blocks both targeting (say)
# module.rds.aws_secretsmanager_secret.db_credentials -- even with
# mutually-exclusive for_each conditions -- is a hard "Duplicate import
# configuration" error, confirmed by a real uat apply failure the first
# time this was tried with a separate block per environment. Each map
# below carries every environment that has ever needed this import; the
# for_each picks out only the current workspace's entry (or none), so
# exactly one workspace's id is ever imported by a given apply.
locals {
  db_creds_secret_name = {
    uat  = "wandahealth-uat-db-creds"
    prod = "wandahealth-prod-db-creds"
  }
  dev_auth_secret_name = {
    uat  = "wandahealth-uat-strata-dev-auth-secret"
    prod = "wandahealth-prod-strata-dev-auth-secret"
  }
  edge_shared_secret_name = {
    uat  = "wandahealth-uat-strata-edge-shared-secret"
    prod = "wandahealth-prod-strata-edge-shared-secret"
  }
}

import {
  for_each = contains(keys(local.db_creds_secret_name), terraform.workspace) ? { current = local.db_creds_secret_name[terraform.workspace] } : {}
  to       = module.rds.aws_secretsmanager_secret.db_credentials
  id       = each.value
}

import {
  for_each = contains(keys(local.dev_auth_secret_name), terraform.workspace) ? { current = local.dev_auth_secret_name[terraform.workspace] } : {}
  to       = module.secrets_manager.aws_secretsmanager_secret.app_secrets["strata-dev-auth-secret"]
  id       = each.value
}

import {
  for_each = contains(keys(local.edge_shared_secret_name), terraform.workspace) ? { current = local.edge_shared_secret_name[terraform.workspace] } : {}
  to       = module.secrets_manager.aws_secretsmanager_secret.app_secrets["strata-edge-shared-secret"]
  id       = each.value
}

# Same real uat destroy also left the KMS key the three secrets above are
# encrypted with in "PendingDeletion" -- confirmed by the apply's next
# failure ("Secrets Manager can't decrypt the secret value ... is
# pending deletion"). Cancelled (aws kms cancel-key-deletion, reversible)
# and re-enabled (cancelling only clears the deletion, it leaves the key
# Disabled) -- imported here for the same reason as the secrets above.
# No matching import for the key's alias: aliases don't have a
# pending-deletion state, they're deleted immediately, so the alias was
# actually gone and Terraform can create it fresh with no conflict.
# prod-only, not needed: prod's own KMS key's PendingDeletion window had
# already fully elapsed by the time its apply ran, so it was gone rather
# than restorable, and Terraform created a fresh one with no conflict.
import {
  for_each = terraform.workspace == "uat" ? { kms = "e6abcfcc-cd61-44fe-98f6-cf8aee59e8b2" } : {}
  to       = module.kms.aws_kms_key.this
  id       = each.value
}

# The same destroy (prod this time) also left the VPC flow-log CloudWatch
# log group behind -- log groups have no pending-deletion window, so this
# isn't a restore case, just state drift: the object exists in AWS but
# Terraform's state doesn't know about it, so a plain create hits "already
# exists". prod-only so far; add this environment's name here too if the
# same drift ever recurs elsewhere.
locals {
  flow_log_group_name = {
    prod = "/aws/vpc/flow-logs/wandahealth-prod"
  }
}

# =============================================================================
# Cross-account image promotion (uat -> prod)
# =============================================================================
# A release does NOT rebuild the application image for prod. The image that
# uat already built, scanned, signed and actually ran is copied into prod's
# registry unchanged, preserving its digest so the existing Cosign signature
# still verifies. That is the whole point: what ships to prod is provably the
# same artifact that was tested, not a fresh build of the same source that
# merely ought to be identical.
#
# uat and prod are separate AWS accounts, so this needs a grant on both
# sides -- uat's ECR repository policy and KMS key policy let prod's build
# role in, and prod's own identity policy lets that role make the request.
# Each half is scoped to the other account and, for KMS, further restricted
# to requests arriving via ECR. Neither half grants any write access.
#
# These IDs live here rather than in the per-environment tfvars because
# tfvars are stored in S3 and are not visible in git -- a cross-account trust
# relationship is exactly the kind of decision that should be reviewable in a
# pull request. Account IDs are identifiers, not secrets, and already appear
# in this repo's GitHub Environment variables.
locals {
  promotion_uat_account_id  = "110156538935"
  promotion_prod_account_id = "274520456367"

  # prod's image-push role, named in uat's resource policies.
  promotion_prod_build_role_arn = "arn:aws:iam::${local.promotion_prod_account_id}:role/github-actions-build"

  # Populated only in the workspace that plays each side of the promotion.
  promotion_pull_principals = terraform.workspace == "uat" ? [local.promotion_prod_build_role_arn] : []
  promotion_source_account  = terraform.workspace == "prod" ? local.promotion_uat_account_id : ""
  promotion_source_prefix   = terraform.workspace == "prod" ? "wandahealth-uat-strata" : ""
}

import {
  for_each = contains(keys(local.flow_log_group_name), terraform.workspace) ? { current = local.flow_log_group_name[terraform.workspace] } : {}
  to       = module.vpc.aws_cloudwatch_log_group.flow_log
  id       = each.value
}

module "security_groups" {
  source      = "./modules/networking/security-groups"
  name_prefix = local.name_prefix
  vpc_id      = module.vpc.vpc_id
  vpc_cidr    = var.vpc_cidr
  tags        = local.common_tags
}

module "guardduty" {
  source      = "./modules/security/guardduty"
  name_prefix = local.name_prefix
  tags        = local.common_tags
}

module "waf" {
  source      = "./modules/security/waf"
  name_prefix = local.name_prefix
  alb_arn     = module.alb.alb_arn
  rate_limit  = var.waf_rate_limit
  tags        = local.common_tags
}

module "secrets_manager" {
  source      = "./modules/security/secrets-manager"
  name_prefix = local.name_prefix
  kms_key_arn = module.kms.key_arn
  tags        = local.common_tags

  secret_definitions = {
    strata-dev-auth-secret = {
      description = "Dev auth secret for Strata services"
    }
    strata-edge-shared-secret = {
      description = "Shared secret for strata-connect edge communication"
    }
  }

  secret_values = {
    strata-dev-auth-secret    = var.strata_uat_auth_secret
    strata-edge-shared-secret = var.strata_edge_shared_secret
  }
}

# ── STORAGE ───────────────────────────────────────────────────────────────────
module "s3" {
  source                     = "./modules/storage/s3"
  name_prefix                = local.name_prefix
  kms_key_arn                = module.kms.key_arn
  versioning_enabled         = var.s3_versioning_enabled
  transition_to_ia_days      = var.s3_transition_to_ia_days
  transition_to_glacier_days = var.s3_transition_to_glacier_days
  tags                       = local.common_tags

  # Disposable environments only. develop is what real testing runs against,
  # so it keeps the default protection: a destroy there fails on a non-empty
  # bucket instead of erasing its contents and every object version.
  force_destroy = local.env != "develop"
}

# ── FRONTEND (static SPA hosting) ─────────────────────────────────────────────
module "frontend" {
  source       = "./modules/frontend"
  name_prefix  = local.name_prefix
  tags         = local.common_tags
  alb_dns_name = module.alb.dns_name

  # See module.s3 -- same reasoning, and it bites harder here: CI syncs the
  # built site into this bucket outside Terraform, so it is never empty in an
  # environment that has been deployed to.
  force_destroy = local.env != "develop"
}

# ── AUTH ───────────────────────────────────────────────────────────────────────
module "cognito" {
  source              = "./modules/auth/cognito"
  name_prefix         = local.name_prefix
  callback_urls       = var.cognito_callback_urls
  logout_urls         = var.cognito_logout_urls
  initial_user_emails = var.cognito_initial_user_emails
  tags                = local.common_tags
}

module "api_gateway" {
  source                = "./modules/networking/api-gateway"
  name_prefix           = local.name_prefix
  cognito_issuer_url    = module.cognito.issuer_url
  cognito_app_client_id = module.cognito.app_client_id
  alb_listener_arn      = module.alb.listener_arn
  security_group_id     = module.security_groups.ecs_sg_id
  private_subnet_ids    = module.subnets.private_subnet_ids
  cors_allow_origins    = var.cors_allow_origins
  log_retention_days    = var.log_retention_days
  kms_key_arn           = module.kms.key_arn
  tags                  = local.common_tags

  depends_on = [module.cognito, module.alb]
}

# ── COMPUTE ───────────────────────────────────────────────────────────────────
module "ecr" {
  source                            = "./modules/compute/ecr"
  name_prefix                       = local.name_prefix
  kms_key_arn                       = module.kms.key_arn
  repositories                      = var.ecr_repositories
  tags                              = local.common_tags
  cross_account_pull_principal_arns = local.promotion_pull_principals

  # See module.s3. develop's registry keeps its images even if someone runs a
  # destroy against it; a disposable environment's does not.
  force_delete = local.env != "develop"
}

module "ecs" {
  source              = "./modules/compute/ecs"
  name_prefix         = local.name_prefix
  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.subnets.private_subnet_ids
  security_group_id   = module.security_groups.ecs_sg_id
  execution_role_arn  = module.iam.ecs_execution_role_arn
  task_role_arn       = module.iam.ecs_task_role_arn
  kms_key_arn         = module.kms.key_arn
  log_retention_days  = var.log_retention_days
  services            = local.ecs_services
  bootstrap_image_tag = var.bootstrap_image_tag
  tags                = local.common_tags

  container_environment = {
    STRATA_AUTH_MODE = var.strata_auth_mode
    # Hardcoded false, not var.strata_uat_mode -- confirmed by the app's own
    # Pydantic validator (real startup failures on develop): STRATA_DEV_MODE=true
    # requires both STRATA_AUTH_MODE=dev AND STRATA_ENVIRONMENT=local (the
    # /v1/dev router mints dev tokens only the dev verifier accepts). local.env
    # is never "local" for anything Terraform deploys to AWS, so this can never
    # legitimately be true here -- var.strata_uat_mode (unused anywhere else)
    # was wired to it by mistake, most likely during the same dev->uat rename
    # that also broke strata_dev_auth_secret. Left the variable itself
    # declared (see variables.tf) rather than removing it -- environments/*.tfvars
    # in S3 may still pass a value for it, and Terraform hard-errors on a value
    # for an undeclared variable.
    STRATA_DEV_MODE    = "false"
    STRATA_ENVIRONMENT = local.env
    STRATA_LOG_FORMAT  = "json"
    # otlp, not "none" -- every task now runs an ADOT Collector sidecar
    # (see modules/compute/ecs's aws-otel-collector container) that
    # forwards to X-Ray, exactly the "ADOT Collector sidecar" path
    # strata_engine/core/observability.py's own docstring already
    # documents as the AWS/Fargate option. localhost:4318 reaches that
    # sidecar directly -- containers in the same ECS task share one
    # network namespace under awsvpc mode, no service discovery needed.
    STRATA_OTEL_TRACES_EXPORTER        = "otlp"
    STRATA_OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318"
    STRATA_COGNITO_REGION              = var.aws_region
    STRATA_COGNITO_USER_POOL_ID        = module.cognito.user_pool_id
    STRATA_COGNITO_CLIENT_ID           = module.cognito.app_client_id
    STRATA_CORS_ALLOW_ORIGINS          = join(",", var.cors_allow_origins)
  }

  container_secrets = {
    STRATA_DATABASE_URL       = "${module.rds.db_secret_arn}:database_url::"
    STRATA_DEV_AUTH_SECRET    = "${module.secrets_manager.secret_arns["strata-dev-auth-secret"]}:::"
    STRATA_EDGE_SHARED_SECRET = "${module.secrets_manager.secret_arns["strata-edge-shared-secret"]}:::"
  }

  scheduled_tasks = {
    strata-booking-jobs = {
      ecr_repository_url  = module.ecr.repository_urls["strata-booking"]
      task_cpu            = var.ecs_task_cpu
      task_memory         = var.ecs_task_memory
      schedule_expression = var.booking_jobs_schedule
      command             = ["python", "-m", "strata_booking.jobs"]
    }
  }

  migration_task = {
    ecr_repository_url = module.ecr.repository_urls["strata-core"]
    task_cpu           = var.ecs_task_cpu
    task_memory        = var.ecs_task_memory
    # Not "inv migrate" -- invoke is a [dev]-only dependency
    # (strata.core/pyproject.toml); running alembic directly avoids
    # pulling a dev tool into the deploy-pipeline image for no benefit.
    command = ["alembic", "upgrade", "head"]
  }

  depends_on = [module.alb]
}

# ── MONITORING ───────────────────────────────────────────────────────────────
module "monitoring" {
  source        = "./modules/monitoring/cloudwatch"
  name_prefix   = local.name_prefix
  cluster_name  = module.ecs.cluster_name
  service_names = module.ecs.service_names
  tags          = local.common_tags

  depends_on = [module.ecs]
}

# Real per-environment HA policy, driven directly by workspace rather than
# the opaque per-environment tfvars file (fetched from S3 at CI runtime,
# not visible/reviewable in this repo) -- that file had this hardcoded to
# false for prod, confirmed live via a real terraform apply plan showing
# `multi_az = false`, contradicting the signed-off "Multi-AZ Only" HA
# scope. develop stays single-AZ (cost-optimized, fast-iteration
# environment, never the actual HA concern); uat mirrors prod's
# architecture so pre-prod testing is representative of what's actually
# running. var.rds_multi_az itself is left declared but unused below --
# removing it outright risks a stricter Terraform version treating an
# S3 tfvars file's now-unreferenced key as an error rather than a warning.
locals {
  rds_multi_az = terraform.workspace != "develop"
}

# ── DATABASE ──────────────────────────────────────────────────────────────────
module "rds" {
  source                = "./modules/database/rds"
  name_prefix           = local.name_prefix
  vpc_id                = module.vpc.vpc_id
  database_subnet_ids   = module.subnets.database_subnet_ids
  security_group_id     = module.security_groups.rds_sg_id
  kms_key_arn           = module.kms.key_arn
  instance_class        = var.rds_instance_class
  engine_version        = var.rds_engine_version
  db_parameter_family   = var.rds_db_parameter_family
  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_max_allocated_storage
  multi_az              = local.rds_multi_az
  db_name               = var.rds_db_name
  db_username           = var.rds_db_username
  db_port               = var.rds_db_port
  backup_retention_days = var.rds_backup_retention_days
  backup_window         = var.rds_backup_window
  maintenance_window    = var.rds_maintenance_window
  monitoring_interval   = var.rds_monitoring_interval
  deletion_protection   = var.rds_deletion_protection
  skip_final_snapshot   = var.rds_skip_final_snapshot
  tags                  = local.common_tags
}

# ── CI SELF-DIAGNOSIS: let the deploy role read its own ECS task logs ─────────
# github-actions-deploy-<x> is a manually-bootstrapped OIDC role (same
# chicken-and-egg reasoning as github-actions-infra-<env> further up this
# file: the pipeline needs the role before it can run the apply that would
# create it) -- Terraform doesn't create or own it, just attaches one
# additional narrow policy to the EXISTING role via a data-source lookup,
# so this never needs the role to already exist before Terraform can run,
# and never needs a human or an AI session pasting personal AWS
# credentials just to read a log a CI job already has OIDC access to.
#
# Confirmed needed by a real failure: reusable-provision-users.yml's
# "Run provision_users.py as a one-off task" step could see the task's
# exit code but not fetch its actual output on failure -- it only printed
# an `aws logs tail` command for a human to run separately, defeating the
# entire point of running this in CI in the first place. Scoped to
# logs:GetLogEvents/DescribeLogStreams/FilterLogEvents only, and only on
# this workspace's own ECS log group -- read-only, no write/delete, no
# access to any other log group in the account. FilterLogEvents is what
# `aws logs tail` calls: provision-client-users.sh reads the new accounts
# back that way to log in with each one, and without it every UAT run
# failed with AccessDenied after creating the account.
#
# Naming predates this repo's workspace-name convention and was never
# reconciled -- develop's role is "-dev", not "-develop".
locals {
  deploy_role_name = {
    develop = "github-actions-deploy-dev"
    uat     = "github-actions-deploy-uat"
    prod    = "github-actions-deploy-prod"
  }[terraform.workspace]

  # develop/uat's roles were bootstrapped by hand before this repo existed, so
  # they're looked up via a data source. prod's equivalent is created by
  # module.iam in this same apply (no manual bootstrap -- see that module's
  # github_actions_deploy resource), so a data source for it would fail on a
  # first apply ("no such role") since data sources resolve before same-run
  # resource creates complete. Resolve to the module output for prod instead;
  # Terraform still orders module.iam's create before this policy attach,
  # because this local depends on that module output.
  deploy_role_id = terraform.workspace == "prod" ? module.iam.github_actions_deploy_prod_role_name : data.aws_iam_role.github_actions_deploy[0].id
}

data "aws_caller_identity" "current" {}

data "aws_iam_role" "github_actions_deploy" {
  count = terraform.workspace == "prod" ? 0 : 1
  name  = local.deploy_role_name
}

resource "aws_iam_role_policy" "github_actions_deploy_logs_readonly" {
  name = "provision-users-logs-readonly"
  role = local.deploy_role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:GetLogEvents", "logs:DescribeLogStreams", "logs:FilterLogEvents"]
      Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ecs/${local.name_prefix}:*"
    }]
  })
}
