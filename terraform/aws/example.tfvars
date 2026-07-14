# Copy to argus.tfvars (gitignored) and adjust if needed.
# Defaults in variables.tf are sensible; this file exists to document overrides.

region      = "us-west-2"
aws_profile = "argus"

cluster_name    = "argus"
cluster_version = "1.31"

node_instance_types = ["t3.medium", "t3a.medium"]
node_desired_size   = 3
node_min_size       = 1
node_max_size       = 4
