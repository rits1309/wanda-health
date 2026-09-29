terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    # Generates a real, random temporary password per Cognito initial user
    # (modules/auth/cognito) -- Terraform's own aws_cognito_user resource
    # requires an explicit temporary_password, it can't invoke Cognito's
    # own auto-generate-a-password behavior the console offers. This
    # avoids ever needing a human (or an AI session) to invent or supply
    # per-user passwords by hand: the only input required is the list of
    # emails.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
