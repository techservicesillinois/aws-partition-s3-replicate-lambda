# =========================================================
# Data
# =========================================================

data "aws_iam_policy_document" "replicate_user" {
    statement {
        sid    = "S3Bucket"
        effect = "Allow"

        actions = [
            "s3:GetBucketAcl",
            "s3:GetBucketLocation",
            "s3:ListBucket*",
        ]
        resources = [ "arn:${local.dest_partition}:s3:::${var.destination_bucket}" ]
    }

    statement {
        sid    = "S3Objects"
        effect = "Allow"

        actions = [
            "s3:AbortMultipartUpload",
            "s3:DeleteObject*",
            "s3:GetObject*",
            "s3:ListMultipartUploadParts",
            "s3:PutObject*",
        ]
        resources = formatlist(
            "arn:%s:s3:::%s/%s*",
            local.dest_partition,
            var.destination_bucket,
            length(var.source_prefixes) > 0 ? var.source_prefixes : [""]
        )
    }

    dynamic "statement" {
        for_each = var.destination_kms_key == null ? [] : [ var.destination_kms_key ]
        content {
            sid    = "S3KMS"
            effect = "Allow"

            actions = [
                "kms:Decrypt",
                "kms:GenerateDataKey",
            ]
            resources = [ statement.value.arn ]

            condition {
                test     = "ArnLike"
                variable = "kms:EncryptionContext:aws:s3:arn"

                values = concat(
                    [ "arn:${local.dest_partition}:s3:::${var.destination_bucket}" ],
                    formatlist(
                        "arn:%s:s3:::%s/%s*",
                        local.dest_partition,
                        var.destination_bucket,
                        length(var.source_prefixes) > 0 ? var.source_prefixes : [""]
                    ),
                )
            }
        }
    }
}

# =========================================================
# Resources
# =========================================================

resource "aws_iam_user" "replicate" {
    provider = aws.destination

    name = "${var.name}-${local.account_id}"
    path = "/${var.name}/"
}

resource "aws_iam_user_policy" "replicate" {
    provider = aws.destination

    name   = "replicate"
    user   = aws_iam_user.replicate.name
    policy = data.aws_iam_policy_document.replicate_user.json
}
