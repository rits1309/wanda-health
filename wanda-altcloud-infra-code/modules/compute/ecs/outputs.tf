################################################################################
# ECS Module - Outputs
################################################################################

output "cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "cluster_arn" {
  description = "ARN of the ECS cluster"
  value       = aws_ecs_cluster.main.arn
}

output "service_names" {
  description = "Map of long-running service names"
  value       = { for k, v in aws_ecs_service.services : k => v.name }
}

output "task_definition_arns" {
  description = "Map of long-running task definition ARNs"
  value       = { for k, v in aws_ecs_task_definition.services : k => v.arn }
}

output "scheduled_task_definition_arns" {
  description = "Map of scheduled task definition ARNs"
  value       = { for k, v in aws_ecs_task_definition.scheduled : k => v.arn }
}

output "migration_task_definition_arn" {
  description = "ARN of the migration task definition (strata-core)"
  value       = aws_ecs_task_definition.migration.arn
}
