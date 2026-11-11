# The optional S3 gateway endpoint. It lets a VM in a subnet with no internet path reach S3.

# The route tables that the endpoint joins. A subnet uses its own table when it has one, and the
# main table of the VPC otherwise.
data "aws_route_tables" "subnet" {
  count  = var.enable_s3_gateway_endpoint ? 1 : 0
  vpc_id = var.vpc_id

  filter {
    name   = "association.subnet-id"
    values = [var.subnet_id]
  }
}

data "aws_route_tables" "main" {
  count  = var.enable_s3_gateway_endpoint ? 1 : 0
  vpc_id = var.vpc_id

  filter {
    name   = "association.main"
    values = ["true"]
  }
}

locals {
  s3_endpoint_route_table_ids = var.enable_s3_gateway_endpoint ? (
    length(data.aws_route_tables.subnet[0].ids) > 0 ? data.aws_route_tables.subnet[0].ids : data.aws_route_tables.main[0].ids
  ) : []
}

resource "aws_vpc_endpoint" "s3" {
  count             = var.enable_s3_gateway_endpoint ? 1 : 0
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = local.s3_endpoint_route_table_ids

  tags = { Name = "${var.name_prefix}-s3" }
}
