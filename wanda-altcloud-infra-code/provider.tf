provider "aws" {
  region = var.aws_region

  skip_credentials_validation = true
  skip_requesting_account_id  = true

  default_tags {
    tags = local.common_tags
  }
}
