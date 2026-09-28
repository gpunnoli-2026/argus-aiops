variable "org_id" {
  description = "Numeric ID of the gklabs.fyi organization"
  type        = string
}

variable "billing_account" {
  description = "Billing account the new projects link to and the budget watches"
  type        = string
}

variable "seed_project_id" {
  description = "Home project of the stage SAs (0-bootstrap)"
  type        = string
  default     = "gk-argus-boot-seed"
}

variable "prefix" {
  description = "Naming prefix shared by every landing-zone resource (docs/gcp-port-design.md §12)"
  type        = string
  default     = "gk-argus"
}

variable "region" {
  description = "Region for the audit log bucket; matches the cluster"
  type        = string
  default     = "us-west1"
}

variable "budget_amount_usd" {
  description = "Monthly budget across the whole billing account, before credits"
  type        = number
  default     = 100
}

# Budget mail goes to billing admins by default, but gk@ and admin@ have no
# mailboxes (Cloud Identity Free). List a real inbox here or alerts go nowhere.
variable "budget_alert_emails" {
  description = "Extra inboxes for budget alerts (max 5)"
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.budget_alert_emails) <= 5
    error_message = "A budget accepts at most 5 notification channels."
  }
}
