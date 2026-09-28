# Copy to terraform.tfvars (gitignored). Runs as sa-tf-org: see
# docs/gcp-port-design.md §14.

org_id          = "000000000000"
billing_account = "XXXXXX-XXXXXX-XXXXXX"

# gk@ and admin@ have no mailboxes; without a real inbox here, budget alerts
# reach nobody.
budget_alert_emails = ["you@example.com"]

# Defaults, shown for reference:
# budget_amount_usd = 100
# region            = "us-west1"
# prefix            = "gk-argus"
# seed_project_id   = "gk-argus-boot-seed"
