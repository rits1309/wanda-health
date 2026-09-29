################################################################################
# Secrets Manager Module
################################################################################

# Application secrets (shared across ECS services)
resource "aws_secretsmanager_secret" "app_secrets" {
  for_each = var.secret_definitions

  name        = "${var.name_prefix}-${each.key}"
  description = each.value.description
  kms_key_id  = var.kms_key_arn

  recovery_window_in_days = var.recovery_window_in_days

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}"
  })
}

# Secret values
resource "aws_secretsmanager_secret_version" "app_secrets" {
  for_each = var.secret_definitions

  secret_id     = aws_secretsmanager_secret.app_secrets[each.key].id
  secret_string = var.secret_values[each.key]
}

# Secret rotation configuration (optional)
resource "aws_secretsmanager_secret_rotation" "app_secrets" {
  for_each = { for k, v in var.secret_definitions : k => v if v.rotation_lambda_arn != null }

  secret_id           = aws_secretsmanager_secret.app_secrets[each.key].id
  rotation_lambda_arn = each.value.rotation_lambda_arn

  rotation_rules {
    automatically_after_days = each.value.rotation_days
  }
}

# IAM policy document for services that need to read secrets
data "aws_iam_policy_document" "secrets_read" {
  statement {
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [for secret in aws_secretsmanager_secret.app_secrets : secret.arn]
  }

  statement {
    effect = "Allow"
    actions = [
      "kms:Decrypt",
    ]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_policy" "secrets_read" {
  name        = "${var.name_prefix}-secrets-read"
  description = "Allow reading application secrets from Secrets Manager"
  policy      = data.aws_iam_policy_document.secrets_read.json

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-secrets-read-policy"
  })
}
