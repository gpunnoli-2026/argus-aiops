.PHONY: help up down plan kubeconfig kind-up kind-down deploy demo lint fmt

AWS_PROFILE ?= argus
TF_DIR      := terraform/aws
TF          := terraform -chdir=$(TF_DIR)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

## ----- AWS (EKS) -----

up: ## Provision VPC + EKS + S3 and configure kubectl (~15 min)
	$(TF) init -upgrade
	$(TF) apply -auto-approve
	$(MAKE) kubeconfig
	@echo ""
	@echo ">>> Cluster up. REMEMBER: 'make down' when you stop 