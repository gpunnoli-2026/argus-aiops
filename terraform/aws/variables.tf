variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-west-2"
}

variable "aws_profile" {
  description = "AWS CLI profile to use"
  type        = string
  default     = "argus"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "argus"
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.31"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "node_instance_types" {
  description = "Instance types for the spot node group (multiple types = better spot availability)"
  type        = list(string)
  default     = ["t3.medium", "t3a.medium"]
}

variable "node_desired_size" {
  description = "Desired number of worker nodes (3 fits monitoring + boutique + chaos + ML services)"
  type        = number
  default     = 3
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable 