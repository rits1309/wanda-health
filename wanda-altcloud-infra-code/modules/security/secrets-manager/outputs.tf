################################################################################
# Secrets Manager Module - Outputs
################################################################################

output "secret_arns" {
  description = "Map of secret name to ARN"
  value       = { for k, v in aws_secretsmanager_secret.app_secrets : k => v.arn }
}

output "secret_names" {
  description = "Map of secret key to full secret name"
  value       = { for k, v in aws_secretsmanager_secret.app_secrets : k => v.name }
}

output "secrets_read_policy_arn" {
  description = "ARN of the IAM policy that grants read access to all managed secrets"
  value       = aws_iam_policy.secrets_read.arn
}
