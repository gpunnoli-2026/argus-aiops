data "aws_caller_identity" "current" {}

# MLflow artifacts + training datasets
resource "aws_s3_bucket" "artifacts" {
  bucket = "argus-artifacts-${data.aws_caller_identity.current.account_id}"

  # Portfolio project: allow terraform destroy to remove a non-empty bucket
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# IRSA: MLflow pods (ns: mlflow, sa: mlflow) get S3 access to this bucket only
module "mlflow_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.52"

  role_name = "${var.cluster_name}-mlflow-s3"

  role_policy_arns = {
    s3 = aws_iam_policy.mlflow_s3.arn
  }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["mlflow:mlflow", "aiops:retraining"]
    }
  }
}

resource "aws_iam_policy" "mlflow_s3" {
  name = "${var.cluster_name}-mlflow-s3"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.artifacts.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.artifacts.arn}/*"]
      }
    ]
  })
}
