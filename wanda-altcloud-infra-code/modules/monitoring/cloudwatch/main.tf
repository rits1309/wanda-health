################################################################################
# CloudWatch Monitoring Module
#
# Creates:
#   - CloudWatch dashboard with CPU and memory utilization widgets per service
#   - CPU utilization alarms per service
#   - Memory utilization alarms per service
#   - SNS topic for alarm notifications
################################################################################

data "aws_region" "current" {}

################################################################################
# SNS Topic for Alarm Notifications
################################################################################

resource "aws_sns_topic" "alarms" {
  name              = "${var.name_prefix}-ecs-alarms"
  kms_master_key_id = "alias/aws/sns"

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ecs-alarms"
  })
}

################################################################################
# CloudWatch Dashboard
#
# Layout: two rows per service — CPU on the left, Memory on the right.
# Each widget is 12 units wide (half the 24-unit CloudWatch grid) and 6 tall.
# Services are sorted by key for a stable, predictable ordering.
################################################################################

locals {
  # Sort service keys so the dashboard order is deterministic across applies
  sorted_service_keys = sort(keys(var.service_names))

  # Build one pair of widgets (CPU + Memory) per service
  # Each pair occupies a full row (y increments by 6 per service)
  dashboard_widgets = flatten([
    for idx, key in local.sorted_service_keys : [
      # CPU Utilization widget (left column)
      {
        type   = "metric"
        x      = 0
        y      = idx * 6
        width  = 12
        height = 6
        properties = {
          title   = "${var.service_names[key]} — CPU Utilization"
          region  = data.aws_region.current.name
          view    = "timeSeries"
          stacked = false
          stat    = "Average"
          period  = 60
          metrics = [
            ["AWS/ECS", "CPUUtilization",
              "ClusterName", var.cluster_name,
              "ServiceName", var.service_names[key],
              { label = "CPU Avg %" }
            ]
          ]
          yAxis = {
            left = {
              min   = 0
              max   = 100
              label = "Percent"
            }
          }
          annotations = {
            horizontal = [
              {
                label = "Alarm threshold"
                value = var.cpu_alarm_threshold
                color = "#ff6961"
              }
            ]
          }
        }
      },
      # Memory Utilization widget (right column)
      {
        type   = "metric"
        x      = 12
        y      = idx * 6
        width  = 12
        height = 6
        properties = {
          title   = "${var.service_names[key]} — Memory Utilization"
          region  = data.aws_region.current.name
          view    = "timeSeries"
          stacked = false
          stat    = "Average"
          period  = 60
          metrics = [
            ["AWS/ECS", "MemoryUtilization",
              "ClusterName", var.cluster_name,
              "ServiceName", var.service_names[key],
              { label = "Memory Avg %" }
            ]
          ]
          yAxis = {
            left = {
              min   = 0
              max   = 100
              label = "Percent"
            }
          }
          annotations = {
            horizontal = [
              {
                label = "Alarm threshold"
                value = var.memory_alarm_threshold
                color = "#ff6961"
              }
            ]
          }
        }
      }
    ]
  ])
}

resource "aws_cloudwatch_dashboard" "ecs" {
  dashboard_name = "${var.name_prefix}-ecs-utilization"

  dashboard_body = jsonencode({
    start          = "-PT3H"
    periodOverride = "auto"
    widgets        = local.dashboard_widgets
  })
}

################################################################################
# CPU Utilization Alarms (one per service)
################################################################################

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  for_each = var.service_names

  alarm_name          = "${var.name_prefix}-${each.key}-cpu-high"
  alarm_description   = "CPU utilization above ${var.cpu_alarm_threshold}% for ${each.value}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = var.alarm_evaluation_periods
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = var.alarm_period_seconds
  statistic           = "Average"
  threshold           = var.cpu_alarm_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = var.cluster_name
    ServiceName = each.value
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}-cpu-high"
  })
}

################################################################################
# Memory Utilization Alarms (one per service)
################################################################################

resource "aws_cloudwatch_metric_alarm" "memory_high" {
  for_each = var.service_names

  alarm_name          = "${var.name_prefix}-${each.key}-memory-high"
  alarm_description   = "Memory utilization above ${var.memory_alarm_threshold}% for ${each.value}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = var.alarm_evaluation_periods
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = var.alarm_period_seconds
  statistic           = "Average"
  threshold           = var.memory_alarm_threshold
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = var.cluster_name
    ServiceName = each.value
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}-memory-high"
  })
}
