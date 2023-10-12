# =========================================================
# Resources
# =========================================================

resource "aws_secretsmanager_secret" "dest_credentials" {
    name        = coalesce(var.destination_secret_name, "${var.name}-credentials")
    description = "AWS credentials for replicating S3 Objects to the destination. Fields: user, accesskey, secretaccesskey, partition."

    recovery_window_in_days = local.is_debug ? 7 : 30
}
