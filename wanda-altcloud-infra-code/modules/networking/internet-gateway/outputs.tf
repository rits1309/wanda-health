################################################################################
# Internet Gateway Module - Outputs
################################################################################

output "internet_gateway_id" {
  description = "ID of the internet gateway"
  value       = aws_internet_gateway.main.id
}
