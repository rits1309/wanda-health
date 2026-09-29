################################################################################
# CloudWatch Monitoring Module - Outputs
################################################################################

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.ecs.dashboard_name
}

output "dashboard_arn" {
  description = "ARN of the CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.ecs.dashboard_arn
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for ECS alarm notifications"
  value       = aws_sns_topic.alarms.arn
}

output "cpu_alarm_arns" {
  description = "Map of service key to CPU high alarm ARN"
  value       = { for k, v in aws_cloudwatch_metric_alarm.cpu_high : k => v.arn }
}

output "memory_alarm_arns" {
  description = "Map of service key to memory high alarm ARN"
  value       = { for k, v in aws_cloudwatch_metric_alarm.memory_high : k => v.arn }
}
