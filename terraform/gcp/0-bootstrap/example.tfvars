# Copy to terraform.tfvars (gitignored). Applied once, as gk@, from a laptop:
# see docs/gcp-port-design.md §13 for the full sequence.

org_id          = "000000000000"         # gcloud organizations list
billing_account = "XXXXXX-XXXXXX-XXXXXX" # gcloud billing accounts list
admin_principal = "user:you@example.com"

# GitHub API: repos/<owner>/<repo> -> .id and .owner.id
github_repository_id = "000000000"
github_owner_id      = "000000000"

# Defaults, shown for reference:
# seed_project_id = "gk-argus-boot-seed"
# region          = "us-west1"
# prefix          = "gk-argus"
