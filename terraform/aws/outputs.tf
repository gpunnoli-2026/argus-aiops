output "cluster_name" {
  value = module.eks.cluster_name
}

output "region" {
  value = var.region
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --profile ${var.aws_profile}"
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "mlflow_irsa_role_arn" {
  description = "Annotate the mlflow ServiceAccount with this role ARN"
  value       = module.mlflow_irsa.iam_role_arn
}

output "vpc_id" {
  value = module.vpc.vpc_id
}
