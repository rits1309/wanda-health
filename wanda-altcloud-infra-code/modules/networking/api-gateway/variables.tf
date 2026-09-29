################################################################################
# API Gateway Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "cognito_issuer_url" {
  description = "Cognito User Pool issuer URL for JWT validation"
  type        = string
}

variable "cognito_app_client_id" {
  description = "Cognito App Client ID (audience for JWT)"
  type        = string
}

variable "alb_listener_arn" {
  description = "ARN of the ALB HTTP listener to integrate with"
  type        = string
}

variable "security_group_id" {
  description = "Security group ID for the VPC Link"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the VPC Link"
  type        = list(string)
}

variable "cors_allow_origins" {
  description = "List of allowed origins for CORS"
  type        = list(string)
  default     = ["*"]
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for log encryption"
  type        = string
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}
