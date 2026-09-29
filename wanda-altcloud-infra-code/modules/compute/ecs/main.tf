################################################################################
# ECS Module
################################################################################

# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = "${var.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-cluster"
  })
}

# Cluster Capacity Providers
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}

# CloudWatch Log Group for ECS
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/aws/ecs/${var.name_prefix}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ecs-logs"
  })
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

################################################################################
# Long-Running Services (for_each over var.services)
################################################################################

# Task Definitions for long-running services
resource "aws_ecs_task_definition" "services" {
  for_each = var.services

  family                   = "${var.name_prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.task_cpu
  memory                   = each.value.task_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "${var.name_prefix}-${each.key}"
      image     = "${each.value.ecr_repository_url}:${var.bootstrap_image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = each.value.container_port
          protocol      = "tcp"
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:${each.value.container_port}${each.value.health_check_path} || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = each.key
        }
      }

      environment = concat(
        [
          {
            name  = "PORT"
            value = tostring(each.value.container_port)
          }
        ],
        [for k, v in var.container_environment : { name = k, value = v }],
        [for k, v in lookup(var.service_extra_environment, each.key, {}) : { name = k, value = v }]
      )

      secrets = [for k, v in var.container_secrets : { name = k, valueFrom = v }]

      # Only START, not HEALTHY -- this sidecar has no healthcheck defined
      # below, so a HEALTHY condition would never resolve. The app's own
      # OTLP exporter (STRATA_OTEL_EXPORTER_OTLP_ENDPOINT, set alongside
      # STRATA_OTEL_TRACES_EXPORTER=otlp in root main.tf's
      # container_environment) tolerates the collector not being ready for
      # its first few spans -- traces are fire-and-forget, not a startup
      # dependency the app blocks on.
      dependsOn = [
        { containerName = "aws-otel-collector", condition = "START" }
      ]
    },
    # ADOT (AWS Distro for OpenTelemetry) Collector, one per task, sharing
    # this task's network namespace (awsvpc mode) so the app reaches it at
    # plain localhost -- no separate service discovery needed. Runs the
    # image's own baked-in default config (an OTLP receiver on 4317/4318
    # piped to an X-Ray exporter + a CloudWatch EMF metrics exporter), so
    # no custom AOT_CONFIG_CONTENT is supplied here. This is the piece
    # strata_engine/core/observability.py's own module docstring calls out
    # as "an Infrastructure concern, not app code" -- the app side
    # (OTLP export, configurable endpoint) already existed; this sidecar
    # plus the task role's xray:Put* permissions (see
    # modules/security/iam) is what actually completes it end to end.
    {
      name  = "aws-otel-collector"
      image = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      # false, deliberately -- essential=true on a sidecar means ECS stops
      # the ENTIRE task (including the real application container) the
      # moment this one exits for any reason. Tracing is a nice-to-have,
      # not something that should ever be able to take the actual service
      # down; if this container dies, the app keeps serving traffic and
      # simply stops emitting traces until the next task cycle replaces it.
      essential = false

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "${each.key}-adot"
        }
      }
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}"
  })
}

# ECS Services
resource "aws_ecs_service" "services" {
  for_each = var.services

  name            = "${var.name_prefix}-${each.key}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.services[each.key].arn_without_revision
  desired_count   = each.value.desired_count
  launch_type     = "FARGATE"

  # Was previously turned on by hand, per environment, via `aws ecs
  # update-service --enable-execute-command` -- left out of the resource
  # config, an untracked attribute like this is not guaranteed to survive
  # the next terraform apply, so each freshly-applied environment silently
  # lost ECS Exec debugging access. Declared explicitly here instead.
  enable_execute_command = true

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = each.value.target_group_arn
    container_name   = "${var.name_prefix}-${each.key}"
    container_port   = each.value.container_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}"
  })
}

# Auto Scaling Targets
resource "aws_appautoscaling_target" "services" {
  for_each = var.services

  max_capacity       = each.value.max_capacity
  min_capacity       = each.value.min_capacity
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.services[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# CPU Auto Scaling Policies
resource "aws_appautoscaling_policy" "cpu" {
  for_each = var.services

  name               = "${var.name_prefix}-${each.key}-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.services[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.services[each.key].scalable_dimension
  service_namespace  = aws_appautoscaling_target.services[each.key].service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 70.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# Memory Auto Scaling Policies
resource "aws_appautoscaling_policy" "memory" {
  for_each = var.services

  name               = "${var.name_prefix}-${each.key}-memory-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.services[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.services[each.key].scalable_dimension
  service_namespace  = aws_appautoscaling_target.services[each.key].service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = 70.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

################################################################################
# Scheduled Tasks (for_each over var.scheduled_tasks)
################################################################################

resource "aws_ecs_task_definition" "scheduled" {
  for_each = var.scheduled_tasks

  family                   = "${var.name_prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.task_cpu
  memory                   = each.value.task_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "${var.name_prefix}-${each.key}"
      image     = "${each.value.ecr_repository_url}:${var.bootstrap_image_tag}"
      essential = true
      command   = each.value.command

      environment = [for k, v in var.container_environment : { name = k, value = v }]

      secrets = [for k, v in var.container_secrets : { name = k, valueFrom = v }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = each.key
        }
      }
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}"
  })
}

################################################################################
# EventBridge Scheduling for Scheduled Tasks
################################################################################

# IAM role EventBridge assumes to call ecs:RunTask on our behalf
resource "aws_iam_role" "eventbridge_ecs" {
  name = "${var.name_prefix}-eventbridge-ecs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "eventbridge_ecs_run_task" {
  name = "${var.name_prefix}-eventbridge-ecs-run-task"
  role = aws_iam_role.eventbridge_ecs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "ecs:RunTask"
        Resource = [
          for k, v in var.scheduled_tasks :
          "arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:task-definition/${var.name_prefix}-${k}:*"
        ]
        Condition = {
          ArnLike = { "ecs:cluster" = aws_ecs_cluster.main.arn }
        }
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [var.execution_role_arn, var.task_role_arn]
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "scheduled" {
  for_each = var.scheduled_tasks

  name                = "${var.name_prefix}-${each.key}-schedule"
  description         = "Triggers the ${each.key} ECS task on a schedule"
  schedule_expression = each.value.schedule_expression

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}-schedule"
  })
}

resource "aws_cloudwatch_event_target" "scheduled" {
  for_each = var.scheduled_tasks

  rule     = aws_cloudwatch_event_rule.scheduled[each.key].name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.scheduled[each.key].arn
    task_count          = 1
    launch_type         = "FARGATE"
    platform_version    = "LATEST"

    network_configuration {
      subnets          = var.private_subnet_ids
      security_groups  = [var.security_group_id]
      assign_public_ip = false
    }
  }
}

################################################################################
# Migration Task (single resource from var.migration_task)
################################################################################

resource "aws_ecs_task_definition" "migration" {
  family                   = "${var.name_prefix}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.migration_task.task_cpu
  memory                   = var.migration_task.task_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "${var.name_prefix}-migration"
      image     = "${var.migration_task.ecr_repository_url}:${var.bootstrap_image_tag}"
      essential = true
      command   = var.migration_task.command

      environment = [for k, v in var.container_environment : { name = k, value = v }]

      secrets = [for k, v in var.container_secrets : { name = k, valueFrom = v }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "migration"
        }
      }
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-migration"
  })
}
