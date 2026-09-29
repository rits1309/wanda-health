variable "name_prefix" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "cognito_user_pool_arn" {
  description = "ARN of the platform's Cognito user pool -- the ECS task role needs admin_* actions scoped to it so the checked-in provisioning script (run inside the container via ECS Exec or a one-off run-task) can create/manage users."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

# Drives both the github-actions-infra-<environment> role name and its
# trust policy's OIDC sub condition -- e.g. "develop", "uat", "prod".
# Pass terraform.workspace from the root module.
variable "environment" {
  type = string
}


variable "summit_github_repo" {
  description = "org/repo whose OIDC tokens github_actions_summit_deploy trusts"
  type        = string
  default     = "Wanda-Health/summit"
}

variable "frontend_bucket_arn" {
  description = "ARN of the frontend S3 bucket github_actions_summit_deploy may sync the build to"
  type        = string
}

variable "frontend_cloudfront_arn" {
  description = "ARN of the frontend CloudFront distribution github_actions_summit_deploy may invalidate"
  type        = string
}

# develop and uat's equivalents (github-actions-build, github-actions-deploy-dev/-uat)
# were created by hand before this module existed, so only prod's are managed here.
# wanda-altcloud has GitHub's per-repo immutable-subject OIDC customization enabled
# (confirmed via `gh api repos/Wanda-Health/wanda-altcloud/actions/oidc/customization/sub`),
# so the sub claim uses the ID-based repo:Org@orgId/repo@repoId form, not the
# name-based repo:Org/repo form summit_github_repo above uses.
variable "wanda_altcloud_oidc_sub" {
  description = "OIDC subject-claim pattern trusted by github_actions_build/github_actions_deploy (prod only)"
  type        = string
  default     = "repo:Wanda-Health@*/wanda-altcloud@*:environment:prod*"
}

variable "promotion_source_account_id" {
  description = <<-EOT
    AWS account ID holding the registry a prod release is promoted FROM
    (uat's). A release copies the exact signed image uat already tested into
    prod's registry rather than rebuilding it, so prod's build role needs
    pull + decrypt permission against that account. Set only on prod; empty
    elsewhere, which omits the grant entirely.
  EOT
  type        = string
  default     = ""
}

variable "promotion_source_repo_prefix" {
  description = "Repository name prefix in the promotion source account, e.g. wandahealth-uat-strata"
  type        = string
  default     = ""
}
