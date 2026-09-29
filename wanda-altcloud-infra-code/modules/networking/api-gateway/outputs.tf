################################################################################
# API Gateway Module - Outputs
################################################################################

output "api_id" {
  description = "ID of the HTTP API"
  value       = aws_apigatewayv2_api.main.id
}

output "api_endpoint" {
  description = "Invoke URL of the API Gateway (use this as the entry point)"
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "api_arn" {
  description = "ARN of the HTTP API"
  value       = aws_apigatewayv2_api.main.arn
}

output "stage_id" {
  description = "ID of the default stage"
  value       = aws_apigatewayv2_stage.main.id
}

output "vpc_link_id" {
  description = "ID of the VPC Link"
  value       = aws_apigatewayv2_vpc_link.main.id
}
