################################################################################
# Secrets Manager Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for secret encryption"
  type        = string
}

variable "secret_definitions" {
  description = "Map of secrets to create. Key is the secret suffix name, value contains metadata."
  type = map(object({
    description         = optional(string, "")
    rotation_lambda_arn = optional(string, null)
    rotation_days       = optional(number, 30)
  }))
}

variable "secret_values" {
  description = "Map of secret name to secret value (must match keys in secret_definitions)"
  type        = map(string)
  sensitive   = true
}

variable "recovery_window_in_days" {
  description = "Number of days Secrets Manager waits before deleting a secret (0 for immediate, 7-30 for recovery window)"
  type        = number
  default     = 7
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}
