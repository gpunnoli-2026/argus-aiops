# Copy to argus.tfvars (gitignored) and set project_id — it has no default.
# Everything else has a sensible default in variables.tf; this file documents
# the overrides worth knowing about.

project_id = "your-project-id"

region = "us-west1"   # Oregon, same as the AWS side; low-cost tier
zone   = "us-west1-b" # zonal cluster: free-tier management fee

machine_type      = "e2-standard-2"
node_desired_size = 3
node_min_size     = 1
node_max_size     = 4

# LEGACY_DATAPATH is the fallback if an iptables-based chaos experiment
# misbehaves under eBPF. Changing it REPLACES the cluster (~15 min).
datapath_provider = "ADVANCED_DATAPATH"

# Lock the public control-plane endpoint to your own IP if you prefer:
# master_authorized_cidrs = [{ cidr_block = "203.0.113.4/32", display_name = "laptop" }]
