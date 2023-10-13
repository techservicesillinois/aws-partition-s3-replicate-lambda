# =========================================================
# Resources
# =========================================================

resource "aws_cloudwatch_metric_alarm" "errors_hi" {
    alarm_name        = "${var.name}-ErrorsHigh"
    alarm_description = "High number of errors occuring on the ${var.name} lambda."

    comparison_operator = "GreaterThanOrEqualToThreshold"
    threshold           = var.error_alarm_threshold
    evaluation_periods  = 3
    datapoints_to_alarm = 3
    treat_missing_data  = "notBreaching"

    metric_query {
        id    = "m1"
        label = "EventErrors"

        metric {
            namespace  = "AWS/Lambda"
            dimensions = {
                FunctionName = module.this_event.lambda_function_name
            }

            metric_name = "Errors"
            period      = 300
            stat        = "Sum"
            unit        = "Count"
        }
    }

    metric_query {
        id    = "m2"
        label = "QueueErrors"

        metric {
            namespace  = "AWS/Lambda"
            dimensions = {
                FunctionName = module.this_queue.lambda_function_name
            }

            metric_name = "Errors"
            period      = 300
            stat        = "Sum"
            unit        = "Count"
        }
    }

    metric_query {
        id    = "e1"
        label = "TotalErrors"

        expression  = "m1+m2"
        return_data = true
    }

    alarm_actions = compact([
        var.notifications_topic_arn,
    ])
    ok_actions = compact([
        var.notifications_topic_arn,
    ])
}
