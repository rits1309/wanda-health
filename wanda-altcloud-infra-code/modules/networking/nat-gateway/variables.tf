################################################################################
# NAT Gateway Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "IDs of the public subnets to place NAT gateways in"
  type        = list(string)
}

variable "internet_gateway_id" {
  description = "ID of the internet gateway (used for depends_on)"
  type        = string
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
