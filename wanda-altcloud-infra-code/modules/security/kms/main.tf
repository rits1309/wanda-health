data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_kms_key" "this" {
  description             = "${var.name_prefix} encryption key"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "Root"
          Effect    = "Allow"
          Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
          Action    = "kms:*"
          Resource  = "*"
        },
        {
          Sid       = "Logs"
          Effect    = "Allow"
          Principal = { Service = "logs.${data.aws_region.current.name}.amazonaws.com" }
          Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
          Resource  = "*"
        }
      ],
      # Decrypt only, and only when the request reaches KMS through ECR --
      # this key also protects RDS, Secrets Manager and CloudWatch Logs, and
      # the ViaService condition is what stops a cross-account grant made for
      # image promotion from reaching any of those. The Root statement above
      # is untouched, so this account never loses control of its own key.
      # range() rather than a ternary returning [] -- a conditional whose two
      # branches are an empty tuple and a tuple of objects carrying a
      # Condition attribute fails to type-unify ("Inconsistent conditional
      # result types"). Iterating zero or one times sidesteps that entirely.
      [
        for _ in range(length(var.cross_account_ecr_decrypt_principal_arns) > 0 ? 1 : 0) : {
          Sid       = "CrossAccountEcrDecrypt"
          Effect    = "Allow"
          Principal = { AWS = var.cross_account_ecr_decrypt_principal_arns }
          Action    = ["kms:Decrypt", "kms:DescribeKey"]
          Resource  = "*"
          Condition = {
            StringEquals = {
              "kms:ViaService" = "ecr.${data.aws_region.current.name}.amazonaws.com"
            }
          }
        }
      ]
    )
  })

  tags = merge(var.tags, { Name = "${var.name_prefix}-kms" })
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name_prefix}-key"
  target_key_id = aws_kms_key.this.key_id
}
