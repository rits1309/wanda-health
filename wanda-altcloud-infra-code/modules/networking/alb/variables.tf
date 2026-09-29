################################################################################
# ALB Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "public_subnet_ids" {
  description = "IDs of the public subnets for ALB"
  type        = list(string)
}

variable "security_group_id" {
  description = "ID of the ALB security group"
  type        = string
}

variable "services" {
  description = "Map of services for path-based routing"
  type = map(object({
    container_port    = number
    health_check_path = string
    path_pattern      = list(string)
    priority          = number
  }))
}

variable "default_service" {
  description = "Name of the default service (receives unmatched traffic)"
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the HTTPS listener. Leave empty to serve HTTP only (the default until a domain + cert exist) -- when set, HTTP traffic is redirected to HTTPS and routing rules move to the HTTPS listener."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}
