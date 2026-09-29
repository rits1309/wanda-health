################################################################################
# GuardDuty Module - Outputs
################################################################################

output "detector_id" {
  description = "ID of the GuardDuty detector"
  value       = aws_guardduty_detector.main.id
}

output "findings_topic_arn" {
  description = "ARN of the SNS topic for GuardDuty findings"
  value       = aws_sns_topic.findings.arn
}
