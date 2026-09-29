# bucket and region are deliberately not set here -- each environment
# (develop/uat/prod) lives in its own AWS account with its own dedicated
# state bucket, so a single hardcoded value can't serve all three.
#
# Values are supplied via per-environment backend config files:
#   terraform init -backend-config="backends/<env>.hcl"
#
# See backends/develop.hcl, backends/uat.hcl, backends/prod.hcl for the
# actual bucket/region values. CI (terraform.yaml) passes the right file
# automatically based on the environment input.
#
# This is a partial backend configuration, not a missing one. Confirmed by
# a real run: a hardcoded uat bucket here made develop's init fail with a
# cross-account AccessDenied trying to reach it.
terraform {
  backend "s3" {
    key     = "savanna/terraform.tfstate"
    encrypt = true
  }
}
