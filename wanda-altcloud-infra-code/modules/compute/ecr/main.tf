################################################################################
# ECR Module
################################################################################

# ECR Repositories
resource "aws_ecr_repository" "main" {
  for_each = toset(var.repositories)

  name                 = "${var.name_prefix}-${each.value}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.value}"
  })
}

# Cross-account pull policy - read-only, and only where explicitly granted
#
# Pull actions only. Nothing here permits PutImage or any layer upload, so a
# grantee can copy an image out of this registry but can never write into it.
# BatchCheckLayerAvailability is required alongside the two read actions
# because a registry-to-registry copy checks which layers the destination
# already has before transferring.
#
# Note this alone is not sufficient to actually pull: these repositories are
# encrypted with a customer-managed KMS key, so the same principal also needs
# kms:Decrypt on that key (granted in the kms module) plus matching
# permissions in its own account's identity policy. All three are required.
resource "aws_ecr_repository_policy" "cross_account_pull" {
  for_each = length(var.cross_account_pull_principal_arns) > 0 ? toset(var.repositories) : toset([])

  repository = aws_ecr_repository.main[each.key].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CrossAccountPull"
        Effect = "Allow"
        Principal = {
          AWS = var.cross_account_pull_principal_arns
        }
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
          # Read-only metadata. The promotion asserts that the digest it
          # pushed to the destination equals the digest it read here -- that
          # assertion is what turns "we copied an image" into "prod runs the
          # identical artifact uat tested", so it needs to read both.
          "ecr:DescribeImages",
        ]
      }
    ]
  })
}

# Lifecycle Policy - Keep 10 production images, expire untagged after 7 days
resource "aws_ecr_lifecycle_policy" "main" {
  for_each = toset(var.repositories)

  repository = aws_ecr_repository.main[each.key].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 production images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["prod"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
