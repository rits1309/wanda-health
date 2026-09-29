################################################################################
# Internet Gateway Module
################################################################################

resource "aws_internet_gateway" "main" {
  vpc_id = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-igw"
  })
}
