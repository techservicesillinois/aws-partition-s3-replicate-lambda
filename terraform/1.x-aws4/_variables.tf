# =========================================================
# General
# =========================================================

variable "environment" {
    type        = string
    description = "Deployment environment (dev, test, prod, devtest, qa)."

    validation {
        condition     = contains(["dev", "test", "prod", "devtest", "qa"], var.environment)
        error_message = "Must be one of: dev; test; prod; devtest; qa."
    }
}

# =========================================================
# Settings
# =========================================================

variable "destination_bucket" {
    type        = string
    description = "Name of the destination S3 bucket."
}

variable "destination_kms_key" {
    type        = object({
                    arn    = string
                    key_id = string
                })
    description = "KMS Key to use to encrypt destination S3 Objects."
    default     = null
}

variable "destination_secret_name" {
    type        = string
    description = "Name of the secret to create to store the destination credentials. If not specified, it will use '{name}-credentials'."
    default     = null
}

variable "source_bucket" {
    type        = string
    description = "Name of the source S3 Bucket."
}

variable "source_kms_keys" {
    type        = list(object({
                    arn    = string
                    key_id = string
                }))
    description = "KMS Key(s) used to encrypt source S3 objects."
    default     = []
}

variable "source_prefixes" {
    type        = list(string)
    description = "List of prefixes in the source S3 Bucket to replicate. If not specified, all objects are replicated."
    default     = []
}

# =========================================================
# Lambda
# =========================================================

variable "name" {
    type        = string
    description = "Unique name of the function."
    default     = "partitionS3Replicate"
}

variable "description" {
    type        = string
    description = "Description of the function."
    default     = "Replicate S3 Objects between buckets in different partitions."
}

variable "deploy_localzip" {
    type        = string
    description = "Path to the zip file to deploy."
    default     = null
}

variable "deploy_s3zip" {
    type        = object({
                    bucket = string
                    prefix = optional(string, "")
                    latest = optional(bool, false)
                })
    description = "S3 bucket and prefix to the partitionS3Replicate/environment.zip file to deploy."
    default     = null

    validation {
        condition     = var.deploy_s3zip == null ? true : can(regex("^(.+/)?$", var.deploy_s3zip.prefix))
        error_message = "Prefix must be empty or end with a '/'."
    }
}

variable "environment_variables" {
    type        = map(string)
    description = "Extra environment variables to set for the Lambda."
    default     = {}
}

variable "error_alarm_threshold" {
    type        = number
    description = "Number of Lambda errors in 3 consecutive 5min periods before an alarm is triggered."
    default     = 1
}

variable "function_tags" {
    type        = map(string)
    description = "Extra tags to add to the Lambda function only."
    default     = {}
}

variable "notifications_topic_arn" {
    type        = string
    description = "SNS Topic to notify when a large number of errors is recorded on the Lambda."
    default     = null
}

# =========================================================
# Logging
# =========================================================

variable "log_encryption_arn" {
    type        = string
    description = "KMS Key ARN to encrypt to this log group."
    default     = null
}

variable "log_subscription_arn" {
    type        = string
    description = "Lambda function ARN to subscribe to this log group."
    default     = null
}
