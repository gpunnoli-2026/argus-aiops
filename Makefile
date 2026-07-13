.PHONY: up down deploy demo lint

up:        ## Provision EKS and deploy the platform (Phase 0+)
	@echo "TODO(Phase 0): terraform apply + helm install"

down:      ## Tear down all cloud resources — ALWAYS run after a session
	@echo "TODO(Phase 0): terraform destroy"

deploy:    ## Deploy/upgrade platform charts to current kube context
	@echo "TODO(Phase 0): helm upgrade --install"

demo:      ## Inject a chaos fault and watch the incident flow
	@echo "TODO(Phase 4): chaos run + demo script"

lint:
	ruff check services/ ml/
