################################################################################
# ECR Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for ECR encryption"
  type        = string
}

variable "repositories" {
  description = "List of repository names to create"
  type        = list(string)
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}

variable "cross_account_pull_principal_arns" {
  description = <<-EOT
    IAM principals in OTHER AWS accounts allowed to pull (never push) from
    these repositories. Set only on uat, where it names prod's build role:
    a release promotes the exact signed image uat already tested into
    prod's registry rather than rebuilding it, and uat and prod are
    separate accounts, so prod's role needs an explicit grant here. Empty
    on every other environment -- no standing cross-account access exists
    unless this is populated.
  EOT
  type        = list(string)
  default     = []
}

variable "force_delete" {
  description = <<-EOT
    Allow `terraform destroy` to delete these repositories while they still
    contain images. Off by default, so destroying a registry that services are
    still deployed from fails rather than quietly discarding every image.
    Any environment that has been deployed to holds images here, so a destroy
    cannot complete without this. The root module turns it on only for
    environments meant to be disposable -- never for develop.

    Images are immutable here (image_tag_mutability = "IMMUTABLE"), so a
    deleted tag cannot be re-pushed under the same name; deletion is final.
  EOT
  type        = bool
  default     = false
}
