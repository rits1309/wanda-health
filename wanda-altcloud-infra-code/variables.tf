# =============================================================================
# Root Variables
# =============================================================================

# ── General ────────────────────────────────────────────────────────────────────
variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}

variable "map_migrated" {
  description = "AWS MAP migrated tag value"
  type        = string
}

variable "map_migrated_app" {
  description = "AWS MAP migrated app tag value"
  type        = string
}

# No default on purpose, unlike map_migrated/map_migrated_app (which both
# accept "" as an explicit opt-out for that one sub-tag) -- this is the real
# AWS Partner Central migration opportunity ID (tag value format "pc:<id>"),
# and every environment must actually supply it. Because it has no default,
# terraform plan/apply refuses to run at all for any environment whose
# tfvars don't set it -- the same "won't run without it" gate map_migrated
# already relies on, just without an empty-string escape hatch, since this
# tag isn't meant to ever be skipped.
variable "aws_apn_id" {
  description = "AWS Partner Central (APN) migration opportunity ID tag value, e.g. \"pc:8b7ph9xvigste67nw1uspqq8e\" -- required, no default, so nothing plans/applies without it"
  type        = string
}

variable "additional_tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
}

# ── Networking ────────────────────────────────────────────────────────────────
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets"
  type        = list(string)
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets"
  type        = list(string)
}

variable "database_subnet_cidrs" {
  description = "CIDR blocks for database subnets"
  type        = list(string)
}

# ── RDS ───────────────────────────────────────────────────────────────────────
variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
}

variable "rds_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
}

variable "rds_db_parameter_family" {
  description = "DB parameter group family (e.g. postgres16)"
  type        = string
}

variable "rds_allocated_storage" {
  description = "Allocated storage in GB"
  type        = number
}

variable "rds_max_allocated_storage" {
  description = "Maximum allocated storage in GB for autoscaling"
  type        = number
}

variable "rds_multi_az" {
  description = "Unused as of the workspace-driven local.rds_multi_az in main.tf -- kept declared, not deleted, so the per-environment tfvars file (in S3, outside this repo) doesn't need a matching change too."
  type        = bool
}

variable "rds_db_name" {
  description = "Name of the database"
  type        = string
}

variable "rds_db_username" {
  description = "Master username for the database"
  type        = string
}

variable "rds_db_port" {
  description = "Port for the database"
  type        = number
}

variable "rds_backup_retention_days" {
  description = "Number of days to retain backups"
  type        = number
}

variable "rds_backup_window" {
  description = "Preferred backup window (e.g. 03:00-04:00)"
  type        = string
}

variable "rds_maintenance_window" {
  description = "Preferred maintenance window (e.g. sun:04:00-sun:05:00)"
  type        = string
}

variable "rds_monitoring_interval" {
  description = "Enhanced monitoring interval in seconds (0/1/5/10/15/30/60)"
  type        = number
}

variable "rds_deletion_protection" {
  description = "Enable deletion protection"
  type        = bool
}

variable "rds_skip_final_snapshot" {
  description = "Skip final snapshot when destroying"
  type        = bool
}

# ── ECS ───────────────────────────────────────────────────────────────────────
variable "ecs_task_cpu" {
  description = "CPU units for ECS tasks"
  type        = number
}

variable "ecs_task_memory" {
  description = "Memory in MiB for ECS tasks"
  type        = number
}

variable "ecs_desired_count" {
  description = "Desired number of tasks per service"
  type        = number
}

variable "ecs_min_capacity" {
  description = "Minimum number of tasks per service for auto scaling"
  type        = number
}

variable "ecs_max_capacity" {
  description = "Maximum number of tasks per service for auto scaling"
  type        = number
}

variable "booking_jobs_schedule" {
  description = "EventBridge schedule expression for strata-booking-jobs (e.g. rate(15 minutes))"
  type        = string
}

# ── ECR ───────────────────────────────────────────────────────────────────────
variable "ecr_repositories" {
  description = "List of ECR repository names to create"
  type        = list(string)
}

# ── S3 ────────────────────────────────────────────────────────────────────────
variable "s3_versioning_enabled" {
  description = "Enable versioning on S3 buckets"
  type        = bool
}

variable "s3_transition_to_ia_days" {
  description = "Days before transitioning objects to STANDARD_IA"
  type        = number
}

variable "s3_transition_to_glacier_days" {
  description = "Days before transitioning objects to GLACIER"
  type        = number
}

# ── WAF ───────────────────────────────────────────────────────────────────────
variable "waf_rate_limit" {
  description = "Rate limit for WAF rate-based rule (requests per 5 minutes per IP)"
  type        = number
  default     = 2000
}

# ── Observability ─────────────────────────────────────────────────────────────
variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
}

# ── Cognito ───────────────────────────────────────────────────────────────────
variable "cognito_callback_urls" {
  description = "Allowed callback URLs for Cognito app client"
  type        = list(string)
}

variable "cognito_logout_urls" {
  description = "Allowed logout URLs for Cognito app client"
  type        = list(string)
}

variable "cognito_initial_user_emails" {
  description = <<-EOT
    Emails to create as users in the Cognito User Pool -- mirrors the
    "Create user" flow in the console. Terraform generates a real, random
    temporary password per user itself (see modules/auth/cognito's
    random_password) -- no password is ever supplied here, so this holds
    only emails and doesn't need to be sensitive. Pass via GitHub secret
    or -var. Leave as [] to skip user creation.
  EOT
  type        = set(string)
  default     = []
}

# ── API Gateway ───────────────────────────────────────────────────────────────
variable "cors_allow_origins" {
  description = "Allowed origins for API Gateway CORS"
  type        = list(string)
  default     = ["*"]
}

# ── ALB / TLS ─────────────────────────────────────────────────────────────────
variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the ALB's HTTPS listener. Leave empty to serve HTTP only; set once a domain + certificate exist for this environment."
  type        = string
  default     = ""
}

# ── ECS bootstrap ─────────────────────────────────────────────────────────────
variable "bootstrap_image_tag" {
  description = "Image tag used only for the first task definition revision per service (see modules/compute/ecs/variables.tf for why). Must be pushed to each ECR repo once before the first apply."
  type        = string
  default     = "bootstrap"
}

# ── Strata App Config ─────────────────────────────────────────────────────────
# "uat" is not a real value here despite the old default -- confirmed by a
# real UAT startup crash: the app's Pydantic Settings model only accepts
# the literal 'cognito' or 'dev' for auth_mode (STRATA_AUTH_MODE), and
# rejects anything else, "uat" included, with a validation error. Almost
# certainly leftover from the same dev->uat rename that also broke
# STRATA_DEV_MODE (see main.tf's container_environment comment) -- "uat"
# was never a valid choice, whatever the variable's own description used
# to claim. develop's tfvars already override this explicitly to
# "cognito" (confirmed against its live task definition), so this default
# only actually mattered for uat, which doesn't override it.
variable "strata_auth_mode" {
  description = "Auth mode for Strata services -- must be exactly 'cognito' or 'dev' (the app's own Settings model rejects any other value)"
  type        = string
  default     = "cognito"
}

variable "strata_uat_mode" {
  description = "Enable uat mode for Strata services"
  type        = bool
  default     = true
}

variable "strata_uat_auth_secret" {
  description = "uat auth secret for Strata services"
  type        = string
  sensitive   = true
  default     = ""
}

# Legacy name kept declared on purpose. The variable was renamed
# strata_dev_auth_secret -> strata_uat_auth_secret in a uat-specific commit,
# but the shared environments/*.tfvars in S3 (notably prod.tfvars) still pass
# the old name. Terraform warns on a value for an undeclared variable in a
# tfvars file (and hard-errors when it comes via -var); either way it's noise
# that broke the prod plan. Declaring it here absorbs that value harmlessly.
# It is intentionally not wired to anything -- strata_uat_auth_secret is the
# live one used by module.secrets_manager. Remove once every environment's
# tfvars in S3 has been updated to the new name.
variable "strata_dev_auth_secret" {
  description = "Deprecated alias of strata_uat_auth_secret; kept declared so legacy environments/*.tfvars values don't error. Unused."
  type        = string
  sensitive   = true
  default     = ""
}

variable "strata_edge_shared_secret" {
  description = "Shared secret for strata-connect edge communication"
  type        = string
  sensitive   = true
  default     = ""
}
