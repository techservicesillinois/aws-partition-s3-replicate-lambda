# =========================================================
# Data
# =========================================================

data "aws_partition" "current" {}

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

# =========================================================
# Data: Destincation
# =========================================================

data "aws_partition" "destination" {
    provider = aws.destination
}

data "aws_region" "destination" {
    provider = aws.destination
}

data "aws_caller_identity" "destination" {
    provider = aws.destination
}

# =========================================================
# Locals
# =========================================================

locals {
    partition   = data.aws_partition.current.partition
    region_name = data.aws_region.current.name
    account_id  = data.aws_caller_identity.current.account_id

    dest_partition   = data.aws_partition.destination.partition
    dest_region_name = data.aws_region.destination.name
    dest_account_id  = data.aws_caller_identity.destination.account_id

    is_debug = var.environment != "prod"
}
