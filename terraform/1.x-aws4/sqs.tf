# =========================================================
# Resources
# =========================================================

resource "aws_sqs_queue" "objects" {
    name       = "${var.name}-objects.fifo"
    fifo_queue = true

    content_based_deduplication = true
    visibility_timeout_seconds  = 15*60
    message_retention_seconds   = 60*60
}
