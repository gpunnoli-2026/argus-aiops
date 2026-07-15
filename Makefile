.PHONY: help up down plan kubeconfig kind-up kind-down deploy load load-varied load-stop chaos-cpu chaos-podkill chaos-latency chaos-clean grafana train mlflow detector-logs forecaster-logs forecasts incidents scores demo lint fmt

AWS_PROFILE ?= argus
TF_DIR      := terraform/aws
TF          := terraform -chdir=$(TF_DIR)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

## ----- AWS (EKS) -----

up: ## Provision VPC + EKS + S3 and configure kubectl (~15 min)
	$(TF) init -upgrade
	$(TF) apply -auto-approve
	$(MAKE) kubeconfig
	@echo ""
	@echo ">>> Cluster up. REMEMBER: 'make down' when you stop working — EKS bills hourly."

plan: ## Preview infrastructure changes
	$(TF) init -upgrade
	$(TF) plan

down: ## Tear down ALL cloud resources — always run after a session
	@echo ">>> Removing Kubernetes-created load balancers first (they block VPC deletion)..."
	-kubectl get svc -A --no-headers 2>/dev/null | awk '$$3=="LoadBalancer" {print $$1, $$2}' | \
		while read ns name; do kubectl -n $$ns delete svc $$name --timeout=60s; done
	-bash -c "sleep 60"   # wait for ELB network interfaces to release (bash: portable on Windows)
	$(TF) init
	$(TF) destroy -auto-approve
	@echo ">>> All AWS resources destroyed. Verify in console: EKS, EC2, NAT GW, S3."

kubeconfig: ## Point kubectl at the EKS cluster
	@eval "$$($(TF) output -raw kubeconfig_command)"
	kubectl get nodes

## ----- Local (kind) -----

kind-up: ## Create local 3-node kind cluster (free dev loop)
	kind create cluster --config kind/cluster.yaml
	kubectl get nodes

kind-down: ## Delete the local kind cluster
	kind delete cluster --name argus

## ----- Platform -----

deploy: ## Deploy observability, demo app, chaos tooling, Argus services
	bash scripts/deploy.sh

load: ## Start background k6 traffic (2h steady baseline)
	kubectl -n loadgen create configmap k6-scenarios --from-file=loadgen/scenarios/ \
		--dry-run=client -o yaml | kubectl apply -f -
	kubectl -n loadgen delete job k6-steady --ignore-not-found
	kubectl apply -f loadgen/k6-steady-job.yaml

load-varied: ## Start 2.5h multi-regime traffic (idle/ramp/steady/spike) — for model v2 training
	kubectl -n loadgen create configmap k6-scenarios --from-file=loadgen/scenarios/ \
		--dry-run=client -o yaml | kubectl apply -f -
	kubectl -n loadgen delete job k6-varied --ignore-not-found
	kubectl apply -f loadgen/k6-varied-job.yaml

load-stop: ## Stop background traffic
	kubectl -n loadgen delete job k6-steady k6-varied --ignore-not-found

chaos-cpu: ## Inject 5m CPU stress on cartservice
	-kubectl delete -f chaos/cpu-stress.yaml --ignore-not-found 2>/dev/null
	kubectl apply -f chaos/cpu-stress.yaml

chaos-podkill: ## Kill one recommendationservice pod
	-kubectl delete -f chaos/pod-kill.yaml --ignore-not-found 2>/dev/null
	kubectl apply -f chaos/pod-kill.yaml

chaos-latency: ## Inject 5m of 500ms latency on productcatalogservice
	-kubectl delete -f chaos/network-delay.yaml --ignore-not-found 2>/dev/null
	kubectl apply -f chaos/network-delay.yaml

chaos-clean: ## Remove all chaos experiments
	kubectl -n chaos delete stresschaos,podchaos,networkchaos --all

grafana: ## Port-forward Grafana to http://localhost:3000
	kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80

## ----- ML -----

train: ## Train anomaly models on recent Prometheus data, register in MLflow
	kubectl -n aiops create configmap argus-training-code --from-file=ml/training/train_anomaly.py \
		--dry-run=client -o yaml | kubectl apply -f -
	kubectl -n aiops delete job argus-train-anomaly --ignore-not-found
	kubectl apply -f ml/training/train-job.yaml
	kubectl -n aiops wait --for=condition=complete --timeout=15m job/argus-train-anomaly || \
		(kubectl -n aiops logs job/argus-train-anomaly --tail=30; exit 1)
	kubectl -n aiops logs job/argus-train-anomaly --tail=5

mlflow: ## Port-forward MLflow UI to http://localhost:5000
	kubectl -n mlflow port-forward svc/mlflow 5000:5000

detector-logs: ## Tail the anomaly-detector logs
	kubectl -n aiops logs deploy/anomaly-detector -f --tail=50

forecaster-logs: ## Tail the capacity-forecaster logs
	kubectl -n aiops logs deploy/capacity-forecaster -f --tail=50

forecasts: ## Show current capacity forecasts
	kubectl -n aiops exec deploy/capacity-forecaster -- python -c "import requests;print(requests.get('http://localhost:8080/forecasts').text)"

incidents: ## Show correlated incidents
	kubectl -n aiops exec deploy/alert-correlator -- python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/incidents').read().decode())"

scores: ## Show current anomaly scores
	kubectl -n aiops exec deploy/anomaly-detector -- python -c "import requests;print(requests.get('http://localhost:8080/scores').text)"

demo: ## Inject a chaos fault and watch the incident flow
	@echo "TODO(Phase 4): chaos run + demo script"

## ----- Code quality -----

lint: ## Lint Python services
	ruff check services/ ml/

fmt: ## Format Terraform
	terraform fmt -recursive terraform/
