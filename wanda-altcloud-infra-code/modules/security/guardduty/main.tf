################################################################################
# GuardDuty Module
################################################################################

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# GuardDuty Detector
resource "aws_guardduty_detector" "main" {
  enable = true

  datasources {
    s3_logs {
      enable = true
    }
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-guardduty"
  })

  # AWS's GuardDuty TagResource API rejects this detector's own ARN with
  # "BadRequestException: Invalid input resource arn" -- confirmed by a
  # real apply failure (develop, 2026-09-09) that got this far after every
  # other resource's tags updated successfully, so it's specific to this
  # one resource, not the tags themselves or how they're built. Terraform
  # still sets tags on create (the block above), which is what's actually
  # live today; this only stops it from re-attempting an UpdateResource
  # call that AWS itself won't accept, which would otherwise fail every
  # future apply that changes any tag (like the new required aws-apn-id
  # one) at this exact step.
  lifecycle {
    ignore_changes = [tags, tags_all]
  }
}

# SNS Topic for GuardDuty Findings
resource "aws_sns_topic" "findings" {
  name              = "${var.name_prefix}-guardduty-findings"
  kms_master_key_id = "alias/aws/sns"

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-guardduty-findings"
  })
}

resource "aws_sns_topic_policy" "findings" {
  arn = aws_sns_topic.findings.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowEventBridgePublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.findings.arn
      }
    ]
  })
}

# EventBridge Rule for High-Severity Findings (severity >= 7)
resource "aws_cloudwatch_event_rule" "high_severity" {
  name        = "${var.name_prefix}-guardduty-high-severity"
  description = "Capture GuardDuty findings with severity >= 7"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
    detail = {
      severity = [{ numeric = [">=", 7] }]
    }
  })

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-guardduty-high-severity"
  })
}

resource "aws_cloudwatch_event_target" "sns" {
  rule      = aws_cloudwatch_event_rule.high_severity.name
  target_id = "guardduty-findings-to-sns"
  arn       = aws_sns_topic.findings.arn
}
