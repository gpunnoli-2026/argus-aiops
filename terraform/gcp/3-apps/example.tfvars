# Optional overrides only: nothing here is required. The project comes from
# 1-org and the region, network and CIDRs from 2-networks, so `make up
# CLOUD=gcp` runs with no tfvars at all. Copy to terraform.tfvars (gitignored)
# to change any of these.

zone = "us-west1-b" # zonal cluster: free-tier management fee; must be in us-west1

machine_type = "e2-standard-2"
# Spot nodes are ~60-90% cheaper. Free-trial billing accounts have a Spot quota
# of zero in some regions — if apply fails on quota, set this to false.
use_spot          = true
node_desired_size = 3
node_min_size     = 1
node_max_size     = 4 # free trials cap concurrent vCPUs at ~8: 4 x e2-standard-2

# LEGACY_DATAPATH is the fallback if an iptables-based chaos experiment
# misbehaves under eBPF. Changing it REPLACES the cluster (~15 min).
datapath_provider = "ADVANCED_DATAPATH"

# Lock the public control-plane endpoint to your own IP if you prefer:
# master_authorized_cidrs = [{ cidr_block = "203.0.113.4/32", display_name = "laptop" }]
