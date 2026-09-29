################################################################################
# WAF Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "alb_arn" {
  description = "ARN of the ALB to associate with WAF"
  type        = string
}

variable "rate_limit" {
  description = "Rate limit for rate-based rule (requests per 5 minutes)"
  type        = number
  default     = 2000
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
