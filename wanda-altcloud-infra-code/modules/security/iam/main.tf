data "aws_caller_identity" "current" {}

resource "aws_iam_role" "ecs_execution" {
  name = "${var.name_prefix}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_kms" {
  name = "${var.name_prefix}-ecs-exec-kms"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [var.kms_key_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = ["arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}-*"]
      }
    ]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "${var.name_prefix}-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "${var.name_prefix}-ecs-task-policy"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.name_prefix}-*", "arn:aws:s3:::${var.name_prefix}-*/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:*"]
        Resource = ["arn:aws:sqs:*:${data.aws_caller_identity.current.account_id}:${var.name_prefix}-*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [var.kms_key_arn]
      },
      # ECS Exec (aws ecs execute-command) needs these on the TASK role, not
      # the execution role -- without them the exec agent inside the
      # container starts (enableExecuteCommand=true is enough for that) but
      # can never open its SSM channel, failing with
      # TargetNotConnectedException. Found missing here after it was patched
      # by hand on develop's task role but never landed in Terraform, so
      # every freshly-applied environment (UAT, and prod later) inherited
      # the same gap.
      {
        Effect   = "Allow"
        Action   = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
        Resource = ["*"]
      },
      # The checked-in user-provisioning script (strata.engine.auth/scripts/
      # provision_users.py) runs INSIDE the container -- via ECS Exec by
      # hand, or as a one-off run-task from the deploy pipeline -- so it
      # authenticates as this task role via boto3's standard credential
      # chain, not a developer's own AWS session. Scoped to this one pool,
      # not "*", since these are real admin_* actions (create/set-password)
      # against real user identities.
      {
        Effect   = "Allow"
        Action   = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminSetUserPassword", "cognito-idp:AdminGetUser", "cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminListGroupsForUser"]
        Resource = [var.cognito_user_pool_arn]
      },
      # The ADOT Collector sidecar (see modules/compute/ecs's
      # aws-otel-collector container) calls these as the task role, not the
      # execution role -- it's the app's own trace/metric data being
      # exported, not anything needed to pull the image or write logs.
      # Matches AWS's own AWSXRayDaemonWriteAccess managed policy's action
      # set exactly, scoped to this account rather than attaching the
      # managed policy wholesale.
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets", "xray:GetSamplingStatisticSummaries"]
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_iam_role" "lambda_execution" {
  name = "${var.name_prefix}-lambda-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role" "config" {
  name = "${var.name_prefix}-config-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "config.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole"
}

# ── CI/CD: the role this repo's own pipeline (terraform.yaml) assumes to
# provision everything is NOT managed by Terraform. It is a BOOTSTRAP
# prerequisite created manually once per account.
#
# Why not managed here: this role is the identity the pipeline uses to *run*
# Terraform. If Terraform owned it we'd have a chicken-and-egg on a fresh
# account (the pipeline needs the role before it can run the apply that would
# create it) and a `destroy` would delete its own running credentials
# mid-run. Managing it here also caused repeated EntityAlreadyExists
# conflicts and OIDC assume-role failures.
#
# Create manually per account (develop/uat/prod):
#   role name:           github-actions-infra-<env>
#   inline policy name:  terraform-infrastructure-<env>
#   trust: OIDC federated to token.actions.githubusercontent.com with
#     sub = repo:Wanda-Health@*/wanda-altcloud-infra-code@*:environment:<env>*
#     (trailing * so both the <env> plan/apply job AND the <env>-destroy job
#      can assume the same role)
#   aud = sts.amazonaws.com

# ── CI/CD: the role summit's own pipeline assumes to build and deploy the
# frontend -- separate from the infra bootstrap role above (this repo's own
# pipeline) and from wanda-altcloud's github-actions-build/deploy roles,
# since summit is its own repo with its own OIDC trust. Deliberately
# narrow (S3 sync + CloudFront invalidation only) rather than the broad
# github_actions_infra grant above -- this pipeline only ever needs to
# push static files and bust the CDN cache, never touch other AWS
# resources.
resource "aws_iam_role" "github_actions_summit_deploy" {
  name = "github-actions-summit-deploy-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "GitHubActionsOIDC"
      Effect = "Allow"
      Principal = {
        Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # Plain name-based sub claim, NOT the ID-based @orgId/@repoId
          # format github_actions_infra above uses -- confirmed by a real
          # query to the summit repo's own /actions/oidc/customization/sub
          # endpoint, which returned "repo:Wanda-Health/summit" with no @id
          # anywhere. This is per-repo, not a fixed org/account-wide
          # setting -- copying github_actions_infra's pattern here without
          # checking summit's own endpoint first caused a real
          # AssumeRoleWithWebIdentity failure (retried 12 times, all
          # denied) during the Summit deploy pipeline's first real run.
          "token.actions.githubusercontent.com:sub" = "repo:${var.summit_github_repo}:environment:${var.environment}"
        }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "github_actions_summit_deploy" {
  name = "summit-frontend-deploy-${var.environment}"
  role = aws_iam_role.github_actions_summit_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SyncBuildToS3"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [var.frontend_bucket_arn, "${var.frontend_bucket_arn}/*"]
      },
      {
        Sid      = "InvalidateCache"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
        Resource = [var.frontend_cloudfront_arn]
      },
    ]
  })
}

################################################################################
# github-actions-build / github-actions-deploy-prod
#
# develop and uat already have manually-bootstrapped equivalents
# (github-actions-build, github-actions-deploy-dev/-uat) created before this
# module existed. Prod is a brand-new account with no such history, so its
# roles are created here instead of by hand -- per explicit direction that
# every prod IAM role must be Terraform-managed, with no manual
# `aws iam create-role` regardless of who happens to be holding valid
# credentials at the time.
################################################################################

data "aws_region" "current" {}

resource "aws_iam_role" "github_actions_build" {
  count = var.environment == "prod" ? 1 : 0

  name = "github-actions-build"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "GitHubActionsOIDC"
      Effect    = "Allow"
      Principal = { Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = var.wanda_altcloud_oidc_sub }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "github_actions_build" {
  count = var.environment == "prod" ? 1 : 0

  name = "ecr-push-prod"
  role = aws_iam_role.github_actions_build[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:DescribeRepositories",
        ]
        Resource = "arn:aws:ecr:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:repository/${var.name_prefix}-strata-*"
      },
    ]
  })
}

# The prod half of the image-promotion grant. A cross-account pull needs
# permission on BOTH sides: the source account's ECR repository policy and KMS
# key policy admit this role, and this policy lets the role make the request
# at all. Deliberately a separate policy from the push one above rather than
# extra statements inside it -- a standing grant into another account should
# be visible and revocable on its own, not buried in the policy that also
# governs ordinary pushes.
#
# Read-only by construction: no push or layer-upload action appears here, so
# this can copy an image out of the source account but never write into it.
resource "aws_iam_role_policy" "github_actions_build_promotion_pull" {
  count = var.environment == "prod" && var.promotion_source_account_id != "" ? 1 : 0

  name = "ecr-pull-promotion-source"
  role = aws_iam_role.github_actions_build[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcrPullFromPromotionSource"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
          "ecr:DescribeRepositories",
          "ecr:DescribeImages",
        ]
        Resource = "arn:aws:ecr:${data.aws_region.current.name}:${var.promotion_source_account_id}:repository/${var.promotion_source_repo_prefix}-*"
      },
      {
        Sid    = "KmsDecryptPromotionSourceImages"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        # The source key's ID isn't knowable from this account, so this is
        # scoped by account plus the ViaService condition instead -- the same
        # constraint the source key policy itself enforces.
        Resource = "arn:aws:kms:${data.aws_region.current.name}:${var.promotion_source_account_id}:key/*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ecr.${data.aws_region.current.name}.amazonaws.com"
          }
        }
      },
    ]
  })
}

resource "aws_iam_role" "github_actions_deploy" {
  count = var.environment == "prod" ? 1 : 0

  name = "github-actions-deploy-prod"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "GitHubActionsOIDC"
      Effect    = "Allow"
      Principal = { Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = var.wanda_altcloud_oidc_sub }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  count = var.environment == "prod" ? 1 : 0

  name = "ecs-deploy-prod"
  role = aws_iam_role.github_actions_deploy[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcsDeploy"
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "ecs:RegisterTaskDefinition",
          "ecs:UpdateService",
          "ecs:RunTask",
          "ecs:DescribeTasks",
          "ecs:ListTasks",
          "ecs:TagResource",
        ]
        Resource = "*"
      },
      {
        Sid      = "PassEcsRoles"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.name_prefix}-*"
      },
    ]
  })
}
