# =========================================================
# Resources
# =========================================================

resource "aws_cloudwatch_log_subscription_filter" "this_event" {
    count = var.log_subscription_arn == null ? 0 : 1

    name           = uuid()
    log_group_name = module.this_event.lambda_cloudwatch_log_group_name

    destination_arn = var.log_subscription_arn
    filter_pattern  = ""
    distribution    = "ByLogStream"

    lifecycle {
        ignore_changes = [ name ]
    }
}

resource "aws_cloudwatch_log_subscription_filter" "this_queue" {
    count = var.log_subscription_arn == null ? 0 : 1

    name           = uuid()
    log_group_name = module.this_queue.lambda_cloudwatch_log_group_name

    destination_arn = var.log_subscription_arn
    filter_pattern  = ""
    distribution    = "ByLogStream"

    lifecycle {
        ignore_changes = [ name ]
    }
}
