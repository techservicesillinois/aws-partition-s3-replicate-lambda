# =========================================================
# Data
# =========================================================

data "aws_s3_object" "this" {
    count = var.deploy_s3zip == null ?  0 : 1

    bucket = var.deploy_s3zip.bucket
    key    = "${var.deploy_s3zip.prefix}partitionS3Replicate/${var.environment}.zip"
}

data "aws_iam_policy_document" "this_event" {
    statement {
        sid    = "SQS"
        effect = "Allow"

        actions = [
            "sqs:GetQueueAttributes",
            "sqs:SendMessage",
        ]
        resources = [ aws_sqs_queue.objects.arn ]
    }
}

data "aws_iam_policy_document" "this_queue" {
    statement {
        sid    = "ObjectsDynamoDB"
        effect = "Allow"

        actions = [
            "dynamodb:BatchGetItem",
            "dynamodb:BatchWriteItem",
            "dynamodb:ConditionCheckItem",
            "dynamodb:DeleteItem",
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:Query",
            "dynamodb:Scan",
            "dynamodb:UpdateItem",
        ]
        resources = [ aws_dynamodb_table.objects.arn ]
    }

    statement {
        sid    = "ObjectsSQS"
        effect = "Allow"

        actions = [
            "sqs:ChangeMessageVisibility",
            "sqs:GetQueueAttributes",
            "sqs:DeleteMessage",
            "sqs:ReceiveMessage",
        ]
        resources = [ aws_sqs_queue.objects.arn ]
    }

    statement {
        sid    = "CredsSecretsManager"
        effect = "Allow"

        actions = [
            "secretsmanager:GetSecretValue",
        ]
        resources = [ aws_secretsmanager_secret.dest_credentials.arn ]
    }

    statement {
        sid    = "S3Bucket"
        effect = "Allow"

        actions = [
            "s3:GetBucketAcl",
            "s3:GetBucketLocation",
            "s3:ListBucket*",
        ]
        resources = [ "arn:${local.partition}:s3:::${var.source_bucket}" ]
    }

    statement {
        sid    = "S3Objects"
        effect = "Allow"

        actions = [
            "s3:GetObject*",
        ]
        resources = formatlist(
            "arn:%s:s3:::%s/%s*",
            local.partition,
            var.source_bucket,
            length(var.source_prefixes) > 0 ? var.source_prefixes : [""]
        )
    }

    dynamic "statement" {
        for_each = length(var.source_kms_keys) > 0 ? [ var.source_kms_keys ] : []
        content {
            sid    = "S3KMS"
            effect = "Allow"

            actions = [
                "kms:Decrypt",
            ]
            resources = statement.value[*].arn

            condition {
                test     = "ArnLike"
                variable = "kms:EncryptionContext:aws:s3:arn"

                values = concat(
                    [ "arn:${local.partition}:s3:::${var.source_bucket}" ],
                    formatlist(
                        "arn:%s:s3:::%s/%s*",
                        local.partition,
                        var.source_bucket,
                        length(var.source_prefixes) > 0 ? var.source_prefixes : [""]
                    ),
                )
            }
        }
    }
}

# =========================================================
# Modules
# =========================================================

module "this_event" {
    source  = "terraform-aws-modules/lambda/aws"
    version = "7.4.0"

    function_name = var.name
    description   = var.description
    handler       = "partition_s3_replicate.event_handler"
    runtime       = "python3.11"
    memory_size   = 128
    timeout       = 30
    function_tags = var.function_tags

    environment_variables = merge(
        var.environment_variables,
        {
            LOGGING_LEVEL = local.partition == "aws" || local.is_debug ? "DEBUG" : "INFO"


            DEST_BUCKET        = var.destination_bucket
            DEST_BUCKET_REGION = local.dest_region_name
            DEST_KMS_KEY       = var.destination_kms_key == null ? "" : var.destination_kms_key.key_id
            DEST_SECRET        = aws_secretsmanager_secret.dest_credentials.name

            OBJECTS_QUEUE = aws_sqs_queue.objects.url
            OBJECTS_TABLE = aws_dynamodb_table.objects.name
        },
    )

    create_package         = false
    local_existing_package = var.deploy_s3zip == null ? coalesce(var.deploy_localzip, "${path.module}/../../dist/partitionS3Replicate.zip") : null
    s3_existing_package    = var.deploy_s3zip == null ? null : {
        bucket     = data.aws_s3_object.this[0].bucket
        key        = data.aws_s3_object.this[0].key
        version_id = data.aws_s3_object.this[0].version_id
    }

    cloudwatch_logs_retention_in_days = local.is_debug ? 7 : 30
    cloudwatch_logs_kms_key_id        = var.log_encryption_arn
    logging_log_format                = "JSON"
    logging_application_log_level     = local.is_debug ? "DEBUG" : "INFO"

    create_current_version_async_event_config   = false
    create_current_version_allowed_triggers     = false
    create_unqualified_alias_allowed_triggers   = true
    create_unqualified_alias_async_event_config = true

    allowed_triggers = {
        S3Notification = {
            principal  = "events.amazonaws.com"
            source_arn = aws_cloudwatch_event_rule.object_events.arn
        }
    }

    role_name          = "${var.name}-${local.region_name}"
    attach_policy_json = true
    policy_json        = data.aws_iam_policy_document.this_event.json
}

module "this_queue" {
    source  = "terraform-aws-modules/lambda/aws"
    version = "7.4.0"

    function_name = "${var.name}-queue"
    description   = var.description
    handler       = "partition_s3_replicate.queue_handler"
    runtime       = "python3.11"
    memory_size   = 128
    timeout       = 15*60
    function_tags = var.function_tags

    environment_variables = merge(
        var.environment_variables,
        {
            LOGGING_LEVEL = local.partition == "aws" || local.is_debug ? "DEBUG" : "INFO"

            DEST_BUCKET        = var.destination_bucket
            DEST_BUCKET_REGION = local.dest_region_name
            DEST_KMS_KEY       = var.destination_kms_key == null ? "" : var.destination_kms_key.key_id
            DEST_SECRET        = aws_secretsmanager_secret.dest_credentials.name

            OBJECTS_QUEUE = aws_sqs_queue.objects.url
            OBJECTS_TABLE = aws_dynamodb_table.objects.name
        },
    )

    create_package         = false
    local_existing_package = var.deploy_s3zip == null ? coalesce(var.deploy_localzip, "${path.module}/../../dist/partitionS3Replicate.zip") : null
    s3_existing_package    = var.deploy_s3zip == null ? null : {
        bucket     = data.aws_s3_object.this[0].bucket
        key        = data.aws_s3_object.this[0].key
        version_id = data.aws_s3_object.this[0].version_id
    }

    cloudwatch_logs_retention_in_days = local.is_debug ? 7 : 30
    cloudwatch_logs_kms_key_id        = var.log_encryption_arn
    logging_log_format                = "JSON"
    logging_application_log_level     = local.is_debug ? "DEBUG" : "INFO"

    create_current_version_async_event_config   = false
    create_current_version_allowed_triggers     = false
    create_unqualified_alias_allowed_triggers   = true
    create_unqualified_alias_async_event_config = true

    allowed_triggers = {
        ObjectsQueue = {
            principal  = "sqs.amazonaws.com"
            source_arn = aws_sqs_queue.objects.arn
        }
    }
    event_source_mapping = {
        sqs = {
            event_source_arn        = aws_sqs_queue.objects.arn
            function_response_types = [ "ReportBatchItemFailures" ]
            batch_size              = 5
        }
    }

    role_name          = "${var.name}-queue-${local.region_name}"
    attach_policy_json = true
    policy_json        = data.aws_iam_policy_document.this_queue.json
}
