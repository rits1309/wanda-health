################################################################################
# S3 Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for bucket encryption"
  type        = string
}

variable "versioning_enabled" {
  description = "Enable versioning on S3 buckets"
  type        = bool
}

variable "transition_to_ia_days" {
  description = "Days before transitioning objects to STANDARD_IA"
  type        = number
}

variable "transition_to_glacier_days" {
  description = "Days before transitioning objects to GLACIER"
  type        = number
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}

variable "force_destroy" {
  description = <<-EOT
    Allow `terraform destroy` to delete these buckets while they still hold
    objects. Off by default, so an accidental destroy of a bucket with
    contents fails loudly with BucketNotEmpty rather than silently erasing
    them. The root module turns this on only for environments that are meant
    to be disposable -- never for develop, which is the environment real
    testing runs against.

    Both buckets here are versioned, and force_destroy removes every object
    version, not just current ones. Treat enabling it as equivalent to
    authorising permanent deletion of the bucket's entire history.
  EOT
  type        = bool
  default     = false
}
