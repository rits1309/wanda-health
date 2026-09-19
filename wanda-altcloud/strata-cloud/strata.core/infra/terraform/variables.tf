variable "aws_region" {
  description = "AWS region the RDS instance lives in."
  type        = string
  default     = "eu-west-2"
}

variable "name_prefix" {
  description = "Name prefix applied to all created resources."
  type        = string
  default     = "summit-rds-tunnel"
}

# --- Pre-existing resources this config REFERENCES (never manages) --------

variable "vpc_id" {
  description = "VPC that the RDS instance and tunnel host live in."
  type        = string
}

variable "subnet_id" {
  description = "Public subnet (routes to an internet gateway) to launch the tunnel host in. Needs outbound 443 for the SSM agent to register."
  type        = string
}

variable "rds_security_group_id" {
  description = "The RDS instance's security group. The tunnel host joins it so the SG's self-referencing rule lets it reach the DB on 5432 — no new ingress rules needed."
  type        = string
}

variable "rds_endpoint" {
  description = "RDS endpoint DNS name (used only to render the start-session command in outputs)."
  type        = string
}

variable "rds_port" {
  description = "RDS port."
  type        = number
  default     = 5432
}

# --- Tunnel host knobs ----------------------------------------------------

variable "instance_type" {
  description = "Tunnel host instance type. t3.micro is plenty for a port-forward relay."
  type        = string
  default     = "t3.micro"
}

variable "local_port" {
  description = "Local port the SSM tunnel forwards to (referenced in the output command)."
  type        = number
  default     = 55432
}
