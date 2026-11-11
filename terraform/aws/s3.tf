# The data bucket. The prober writes each record here. Terraform uploads the code zips under code/.

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.name_prefix}-data-${random_id.bucket_suffix.hex}"

  # The bucket holds the experiment data. Destroy fails until you empty it (README.md).
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# The code under code/ has no lifecycle rule. OpenTofu deletes an old zip when a new
# one replaces it (versioning is off), so a rule would only delete the objects that the current
# manifest.json names. The data under experiment=*/ has no lifecycle rule. The logs under
# experiment=*/logs/ get their own rule: 90 days, apart from the rows (spec 0003 section 8.2).
# S3 lifecycle filters match a literal prefix, not a glob, so the rule names the configured
# experiment, the same way local.experiment already names it elsewhere (for example iam.tf).
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-logs"
    status = "Enabled"

    filter {
      prefix = "experiment=${local.experiment}/logs/"
    }

    expiration {
      days = 90
    }
  }
}
