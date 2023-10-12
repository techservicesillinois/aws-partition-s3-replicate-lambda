# =========================================================
# Terraform
# =========================================================

terraform {
    required_version = "~> 1.0"
    required_providers {
        aws = {
            source  = "hashicorp/aws"
            version = "~> 4.9"
        }
    }
}

# =========================================================
# Providers
# =========================================================

provider "aws" {
    region = "us-east-2"
}

provider "aws" {
    alias  = "us_gov_east_1"
    region = "us-gov-east-1"
}

# =========================================================
# Modules
# =========================================================

module "partitionS3Replicate" {
    source = "./module"
    providers = {
        aws = aws
        aws.destination = aws.us_gov_east_1
    }

    environment = "test"

    destination_bucket = "destination-bucket"

    source_bucket = "source-bucket"
}
