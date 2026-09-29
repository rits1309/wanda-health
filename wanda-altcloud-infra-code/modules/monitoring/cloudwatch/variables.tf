################################################################################
# CloudWatch Monitoring Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names (e.g. wandahealth-develop)"
  type        = string
}

variable "cluster_name" {
  description = "Name of the ECS cluster to monitor"
  type        = string
}

variable "service_names" {
  description = "Map of service key to ECS service name (from module.ecs.service_names)"
  type        = map(string)
}

variable "dashboard_refresh_interval" {
  description = "CloudWatch dashboard auto-refresh interval in seconds (60, 300, 900, 3600)"
  type        = number
  default     = 300
}

variable "cpu_alarm_threshold" {
  description = "CPU utilization percentage threshold for CloudWatch alarms"
  type        = number
  default     = 80
}

variable "memory_alarm_threshold" {
  description = "Memory utilization percentage threshold for CloudWatch alarms"
  type        = number
  default     = 80
}

variable "alarm_evaluation_periods" {
  description = "Number of periods over which data is compared to the alarm threshold"
  type        = number
  default     = 2
}

variable "alarm_period_seconds" {
  description = "Period in seconds over which the metric is evaluated"
  type        = number
  default     = 300
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
