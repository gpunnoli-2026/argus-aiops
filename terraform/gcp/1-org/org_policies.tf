# Guardrails inherited by every folder and project. Projects depend on these
# so they are born under them (skipDefaultNetworkCreation in particular).
#
# Google enforces a "secure by default" set on new organizations. Left to
# Google, not managed here:
#   iam.managed.disableServiceAccountKeyCreation  no SA keys (the managed form;
#                                                 people impersonate, CI uses
#                                                 WIF, pods use Workload Identity)
#   iam.disableServiceAccountKeyUpload
#   iam.allowedPolicyMemberDomains                IAM only for gklabs.fyi principals
#   essentialcontacts.managed.allowedContactDomains
#   compute.managed.restrictProtocolForwardingCreationForTypes
# Two of Google's defaults are also ours; imports.tf adopts them rather than
# failing on "already exists".

locals {
  enforced_constraints = toset([
    # Stops the default Compute SA getting project Editor; nodes have their own SA.
    "iam.automaticIamGrantsForDefaultServiceAccounts",
    # No wide-open default VPC in new projects.
    "compute.skipDefaultNetworkCreation",
    # SSH through IAM, never project-wide keys.
    "compute.requireOsLogin",
    "storage.uniformBucketLevelAccess",
    "storage.publicAccessPrevention",
  ])
}

resource "google_org_policy_policy" "enforced" {
  for_each = local.enforced_constraints

  name   = "organizations/${var.org_id}/policies/${each.key}"
  parent = "organizations/${var.org_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }
}

# GKE nodes are already private (enable_private_nodes); egress goes via NAT.
resource "google_org_policy_policy" "no_vm_external_ip" {
  name   = "organizations/${var.org_id}/policies/compute.vmExternalIpAccess"
  parent = "organizations/${var.org_id}"

  spec {
    rules {
      deny_all = "TRUE"
    }
  }
}

resource "google_org_policy_policy" "us_only" {
  name   = "organizations/${var.org_id}/policies/gcp.resourceLocations"
  parent = "organizations/${var.org_id}"

  spec {
    rules {
      values {
        allowed_values = ["in:us-locations"]
      }
    }
  }
}
