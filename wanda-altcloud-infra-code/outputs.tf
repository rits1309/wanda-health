# =============================================================================
# Root Outputs
# =============================================================================

# ── Networking ────────────────────────────────────────────────────────────────
output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of public subnets"
  value       = module.subnets.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value       = module.subnets.private_subnet_ids
}

output "database_subnet_ids" {
  description = "IDs of database subnets"
  value       = module.subnets.database_subnet_ids
}

output "alb_dns_name" {
  description = "DNS name of the ALB"
  value       = module.alb.dns_name
}

output "alb_arn" {
  description = "ARN of the ALB"
  value       = module.alb.alb_arn
}

# ── Security ──────────────────────────────────────────────────────────────────
output "kms_key_arn" {
  description = "ARN of the KMS key"
  value       = module.kms.key_arn
}

# ── Compute ───────────────────────────────────────────────────────────────────
output "ecr_repository_urls" {
  description = "Map of ECR repository URLs"
  value       = module.ecr.repository_urls
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.ecs.cluster_name
}

output "ecs_service_names" {
  description = "Map of ECS service names"
  value       = module.ecs.service_names
}

# ── Database ──────────────────────────────────────────────────────────────────
output "rds_endpoint" {
  description = "Endpoint of the RDS instance"
  value       = module.rds.db_endpoint
  sensitive   = true
}

output "rds_secret_arn" {
  description = "ARN of the Secrets Manager secret for DB credentials"
  value       = module.rds.db_secret_arn
}

# ── Storage ───────────────────────────────────────────────────────────────────
output "s3_assets_bucket_id" {
  description = "ID of the S3 assets bucket"
  value       = module.s3.bucket_id
}

output "s3_logs_bucket_id" {
  description = "ID of the S3 logs bucket"
  value       = module.s3.logs_bucket_id
}

# ── Frontend ──────────────────────────────────────────────────────────────────
output "frontend_bucket_id" {
  description = "ID of the frontend (Summit Coach Portal) origin bucket"
  value       = module.frontend.bucket_id
}

output "frontend_cloudfront_distribution_id" {
  description = "CloudFront distribution ID for the frontend (used for cache invalidation)"
  value       = module.frontend.cloudfront_distribution_id
}

output "frontend_cloudfront_domain_name" {
  description = "CloudFront domain name serving the frontend"
  value       = module.frontend.cloudfront_domain_name
}

# Sensitive -- redacted from plan/apply CLI output by default. Retrieve
# with `terraform output -json cognito_initial_user_passwords` to
# distribute these to the real people behind var.cognito_initial_user_emails.
output "cognito_initial_user_passwords" {
  description = "Map of email to its generated temporary password, for the initial users this apply just created"
  value       = module.cognito.cognito_initial_user_passwords
  sensitive   = true
}
