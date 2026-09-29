output "ecs_execution_role_arn" {
  value = aws_iam_role.ecs_execution.arn
}

output "ecs_task_role_arn" {
  value = aws_iam_role.ecs_task.arn
}

output "lambda_execution_role_arn" {
  value = aws_iam_role.lambda_execution.arn
}

output "config_role_arn" {
  value = aws_iam_role.config.arn
}

output "github_actions_summit_deploy_role_arn" {
  value = aws_iam_role.github_actions_summit_deploy.arn
}

# null outside prod -- github_actions_build/github_actions_deploy only exist there.
output "github_actions_build_role_arn" {
  value = try(aws_iam_role.github_actions_build[0].arn, null)
}

output "github_actions_deploy_prod_role_arn" {
  value = try(aws_iam_role.github_actions_deploy[0].arn, null)
}

output "github_actions_deploy_prod_role_name" {
  value = try(aws_iam_role.github_actions_deploy[0].name, null)
}
