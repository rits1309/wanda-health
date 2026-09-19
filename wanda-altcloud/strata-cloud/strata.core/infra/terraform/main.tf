# SSM-based tunnel host for reaching the private RDS instance from a laptop.
#
# Managed here: IAM role + instance profile, a dedicated egress security group,
# and the EC2 tunnel host. The VPC / subnet / RDS / RDS-SG are pre-existing and
# only referenced via variables, so `terraform destroy` removes the tunnel host
# and its IAM/SG but never touches the database or its network.

# Always launch the current Amazon Linux 2023 AMI (SSM agent preinstalled).
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# --- IAM: let the instance register with Systems Manager ------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "tunnel" {
  name               = "${var.name_prefix}-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# AWS-managed policy that grants exactly what the SSM agent needs — nothing more.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.tunnel.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "tunnel" {
  name = "${var.name_prefix}-profile"
  role = aws_iam_role.tunnel.name
}

# --- Network: egress-only SG so the agent can reach the SSM endpoints -----

# No ingress at all — SSM works over the agent's outbound connection, so the
# host never needs an open inbound port.
resource "aws_security_group" "tunnel" {
  name        = "${var.name_prefix}-sg"
  description = "Egress-only SG for the SSM tunnel host."
  vpc_id      = var.vpc_id

  egress {
    description = "All outbound (SSM endpoints on 443, plus the DB)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-sg" }
}

# --- The tunnel host ------------------------------------------------------

resource "aws_instance" "tunnel" {
  ami           = data.aws_ssm_parameter.al2023.value
  instance_type = var.instance_type
  subnet_id     = var.subnet_id

  iam_instance_profile        = aws_iam_instance_profile.tunnel.name
  associate_public_ip_address = true

  # Two SGs: the dedicated egress SG (for SSM + DB outbound) and the RDS SG
  # (membership satisfies the DB's self-referencing ingress rule).
  vpc_security_group_ids = [
    aws_security_group.tunnel.id,
    var.rds_security_group_id,
  ]

  # Require IMDSv2 (token-based metadata) — a cheap, standard hardening default.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = { Name = "${var.name_prefix}-host" }
}
