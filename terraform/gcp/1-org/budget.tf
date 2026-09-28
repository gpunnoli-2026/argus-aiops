# Email channels live in shared-ops: a budget can only notify through Cloud
# Monitoring channels, and those belong to a project.
resource "google_monitoring_notification_channel" "budget" {
  for_each = toset(var.budget_alert_emails)

  project      = google_project.this["shared-ops"].project_id
  display_name = "Budget alerts (${each.value})"
  type         = "email"
  labels = {
    email_address = each.value
  }

  depends_on = [google_project_service.this["shared-ops/monitoring"]]
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "${var.prefix} monthly"

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_amount_usd)
    }
  }

  budget_filter {
    calendar_period = "MONTH"
    # Measure gross spend. With credits included, a free-trial account shows
    # $0 until the credit runs out, so no alert would ever fire in time.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  dynamic "threshold_rules" {
    for_each = [0.25, 0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  # Warns before the month ends, while there's still time to run `make down`.
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [for c in google_monitoring_notification_channel.budget : c.id]
    disable_default_iam_recipients   = false
  }
}
