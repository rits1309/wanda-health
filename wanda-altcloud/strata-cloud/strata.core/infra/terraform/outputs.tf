output "tunnel_instance_id" {
  description = "EC2 instance id of the tunnel host."
  value       = aws_instance.tunnel.id
}

output "start_session_command" {
  description = "Open the port-forward tunnel. Leave it running in its own terminal."
  value = join(" ", [
    "aws ssm start-session",
    "--region ${var.aws_region}",
    "--target ${aws_instance.tunnel.id}",
    "--document-name AWS-StartPortForwardingSessionToRemoteHost",
    "--parameters '${jsonencode({
      host            = [var.rds_endpoint]
      portNumber      = [tostring(var.rds_port)]
      localPortNumber = [tostring(var.local_port)]
    })}'",
  ])
}

output "seed_command" {
  description = "With the tunnel open, seed the real RDS via localhost."
  value = join(" ", [
    "env",
    "POSTGRES_HOST=localhost",
    "POSTGRES_PORT=${var.local_port}",
    "POSTGRES_SSLMODE=require",
    ".venv/bin/python -m app.seed",
  ])
}
