################################################################################
# Cognito Module - Outputs
################################################################################

output "user_pool_id" {
  description = "ID of the Cognito User Pool"
  value       = aws_cognito_user_pool.main.id
}

output "user_pool_arn" {
  description = "ARN of the Cognito User Pool"
  value       = aws_cognito_user_pool.main.arn
}

output "user_pool_endpoint" {
  description = "Endpoint of the Cognito User Pool"
  value       = aws_cognito_user_pool.main.endpoint
}

output "user_pool_domain" {
  description = "Domain of the Cognito User Pool"
  value       = aws_cognito_user_pool_domain.main.domain
}

output "app_client_id" {
  description = "ID of the Cognito App Client (for Strata.Engine.Auth)"
  value       = aws_cognito_user_pool_client.strata_engine_auth.id
}

output "issuer_url" {
  description = "Issuer URL for JWT validation (used by API Gateway Authorizer)"
  value       = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

data "aws_region" "current" {}

output "initial_user_usernames" {
  description = "Set of Cognito usernames (emails) for all created initial users"
  value       = { for k, v in aws_cognito_user.initial : k => v.username }
}

# Sensitive -- redacted from plan/apply CLI output by default. Retrieve with
# `terraform output -json cognito_initial_user_passwords` (after selecting
# the right workspace) to actually distribute these to the real people
# behind var.initial_user_emails; there's no way around a human being told
# their own new account's password at least once.
output "cognito_initial_user_passwords" {
  description = "Map of email to its generated temporary password, for the initial users this module just created"
  value       = { for email, pw in random_password.initial_user : email => pw.result }
  sensitive   = true
}
