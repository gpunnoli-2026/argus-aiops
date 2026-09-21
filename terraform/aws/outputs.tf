# Every cloud root module exposes the same five outputs. scripts/deploy.sh and
# the Makefile read only these names, so they never learn which cloud they are
# talking to.

output "cluster_name" {
  value = module.eks.cluster_name
}

output "location" {
  description = "Region or zone the cluster runs in"
  value       = var.region
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --profile ${var.aws_profile}"
}

output "artifact_uri" {
  description = "MLflow artifact root"
  value       = "s3://${aws_s3_bucket.artifacts.id}/mlartifacts"
}

output "helm_values" {
  description = "Per-deployment Helm values — valid values YAML as JSON, applied last by deploy.sh"
  value = {
    artifactUri = "s3://${aws_s3_bucket.artifacts.id}/mlartifacts"
    mlflow = {
      serviceAccountAnnotations = {
        "eks.amazonaws.com/role-arn" = module.mlflow_irsa.iam_role_arn
      }
      extraEnv = [
        {
          name  = "AWS_REGION"
          value = var.region
        }
      ]
    }
  }
}

output "vpc_id" {
  value = module.vpc.vpc_id
}
