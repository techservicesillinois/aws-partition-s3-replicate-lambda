
resource "aws_cloudwatch_event_rule" "object_events" {
    name        = "${var.name}-object-events"
    description = "Queue replication of S3 Objects in ${var.source_bucket}."

    event_pattern = jsonencode({
        detail-type = [
            "Object Created", "Object Deleted",
            "Object Tags Added", "Object Tags Deleted",
        ]
        detail = merge(
            {
                bucket = {
                    name = [ var.source_bucket ]
                }
            },
            length(var.source_prefixes) == 0 ? {} : {
                object = {
                    key = [ for p in var.source_prefixes : { prefix = p } ]
                }
            }
        )
    })
}

resource "aws_cloudwatch_event_target" "object_events" {
    rule      = aws_cloudwatch_event_rule.object_events.name
    arn       = module.this_event.lambda_function_arn
}
