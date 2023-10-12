# =========================================================
# Resources
# =========================================================

resource "aws_dynamodb_table" "objects" {
    name         = "${var.name}-objects"
    billing_mode = "PAY_PER_REQUEST"
    hash_key     = "Key"
    range_key    = "VersionId"

    attribute {
        name = "Key"
        type = "S"
    }

    attribute {
        name = "VersionId"
        type = "S"
    }
}
