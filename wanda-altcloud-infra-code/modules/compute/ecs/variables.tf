################################################################################
# ECS Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "private_subnet_ids" {
  description = "IDs of the private subnets for ECS tasks"
  type        = list(string)
}

variable "security_group_id" {
  description = "ID of the ECS security group"
  type        = string
}

variable "execution_role_arn" {
  description = "ARN of the ECS task execution role"
  type        = string
}

variable "task_role_arn" {
  description = "ARN of the ECS task role"
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for log encryption"
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
}

variable "services" {
  description = "Map of long-running ECS services to create"
  type = map(object({
    container_port     = number
    ecr_repository_url = string
    target_group_arn   = string
    task_cpu           = number
    task_memory        = number
    desired_count      = number
    min_capacity       = number
    max_capacity       = number
    health_check_path  = string
  }))
}

variable "scheduled_tasks" {
  description = "Map of scheduled ECS tasks (EventBridge triggered)"
  type = map(object({
    ecr_repository_url  = string
    task_cpu            = number
    task_memory         = number
    schedule_expression = string
    command             = list(string)
  }))
}

variable "migration_task" {
  description = "Configuration for the one-off migration task (strata-core)"
  type = object({
    ecr_repository_url = string
    task_cpu           = number
    task_memory        = number
    command            = list(string)
  })
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}

variable "container_environment" {
  description = "Common environment variables for all ECS service containers"
  type        = map(string)
  default     = {}
}

variable "service_extra_environment" {
  description = "Per-service extra environment variables (map of service name to map of env vars)"
  type        = map(map(string))
  default     = {}
}

variable "container_secrets" {
  description = "Secrets to inject from Secrets Manager/SSM (map of env var name to secret ARN with optional JSON key, e.g. arn:secret:name:key::)"
  type        = map(string)
  default     = {}
}

variable "bootstrap_image_tag" {
  description = <<-EOT
    Image tag used only for the first task definition revision Terraform creates.
    ECR repositories are IMMUTABLE-tag, so this must be pushed once per repository
    before the first apply (the CI/CD pipeline never pushes this tag itself -- it
    tags images <ref_name>-<sha>). After bootstrap, `lifecycle.ignore_changes` on
    the ECS service means Terraform never touches the running image again --
    real deploys are owned entirely by the CI/CD pipeline's fetch-and-patch step.
  EOT
  type        = string
  default     = "bootstrap"
}
