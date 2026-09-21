.PHONY: help up down plan kubeconfig check-cloud check-prereqs check-context kind-up kind-down deploy frontend-public load load-varied load-stop chaos-cpu chaos-podkill chaos-latency chaos-clean grafana grafana-password train rollback mlflow detector-logs forecaster-logs forecasts incidents scores demo test lint fmt

# CLOUD has no default on purpose: `make down` against the wrong cloud is the
# one mistake here that is both easy to make and expensive.
#   make up CLOUD=gcp       provision GKE + GCS
#   make up CLOUD=aws       provision EKS + S3
#   make deploy CLOUD=kind  local cluster, no cloud resources
CLOUD        ?=
AWS_PROFILE  ?= argus
CLOUDS_INFRA := aws gcp
CLOUDS_ALL   := aws gcp kind
TF_DIR       := terraform/$(CLOUD)
TF           := terraform -chdir=$(TF_DIR)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'
	@echo ""
	@echo "  Infrastructure targets need CLOUD=aws|gcp; deploy also accepts CLOUD=kind."

## ----- Guards -----

check-cloud:
	@allowed="$(CLOUDS)"; \
	if [ -z "$(CLOUD)" ]; then \
		echo "ERROR: CLOUD is required (one of: $$allowed)."; \
		echo "       e.g. make $(firstword $(MAKECMDGOALS)) CLOUD=gcp"; exit 1; \
	fi; \
	case " $$allowed " in \
		*" $(CLOUD) "*) ;; \
		*) echo "ERROR: CLOUD='$(CLOUD)' is not one of: $$allowed"; exit 1 ;; \
	esac

check-prereqs:
	@for bin in kubectl helm terraform; do \
		command -v $$bin >/dev/null || { echo "ERROR: $$bin not found in PATH"; exit 1; }; \
	done
	@if [ "$(CLOUD)" = "aws" ]; then \
		command -v aws >/dev/null || { echo "ERROR: aws CLI not found"; exit 1; }; \
	fi
	@if [ "$(CLOUD)" = "gcp" ]; then \
		command -v gcloud >/dev/null || { echo "ERROR: gcloud not found"; exit 1; }; \
		command -v gke-gcloud-auth-plugin >/dev/null || { \
			echo "ERROR: gke-gcloud-auth-plugin not found — kubectl cannot authenticate to GKE."; \
			echo "       gcloud components install gke-gcloud-auth-plugin"; exit 1; }; \
	fi

# Second guard behind check-cloud: CLOUD=aws with a live GKE context passes the
# first one and would then delete the other cluster's load balancers — or, on
# deploy, render kind's StorageClass onto a cloud cluster.
check-context:
	@ctx=$$(kubectl config current-context 2>/dev/null || true); \
	if [ -z "$$ctx" ]; then \
		echo ">>> No kubectl context — skipping in-cluster cleanup."; exit 0; \
	fi; \
	case "$(CLOUD):$$ctx" in \
		aws:arn:aws:eks:*) ;; \
		gcp:gke_*) ;; \
		kind:kind-*) ;; \
		*) echo "ERROR: CLOUD=$(CLOUD) but the live kubectl context is '$$ctx'."; \
		   echo "       Refusing — switch context or fix CLOUD."; exit 1 ;; \
	esac

## ----- Cloud lifecycle (CLOUD=aws|gcp) -----

up: CLOUDS = $(CLOUDS_INFRA)
up: check-cloud check-prereqs ## Provision cluster + object storage, configure kubectl (~15 min)
	$(TF) init -upgrade
	$(TF) apply -auto-approve
	$(MAKE) kubeconfig CLOUD=$(CLOUD)
	@echo ""
	@echo ">>> Cluster up on $(CLOUD). REMEMBER: 'make down CLOUD=$(CLOUD)' — clusters bill hourly."

plan: CLOUDS = $(CLOUDS_INFRA)
plan: check-cloud ## Preview infrastructure changes
	$(TF) init -upgrade
	$(TF) plan

down: CLOUDS = $(CLOUDS_INFRA)
down: check-cloud check-context ## Tear down ALL cloud resources — always run after a session
	@echo ">>> Removing Kubernetes-created load balancers first (they block network deletion)..."
	-kubectl get svc -A --no-headers 2>/dev/null | awk '$$3=="LoadBalancer" {print $$1, $$2}' | \
		while read ns name; do kubectl -n $$ns delete svc $$name --timeout=60s; done
	@echo ">>> Removing PVCs (their disks outlive the cluster and keep billing)..."
	-kubectl delete pvc -A --all --timeout=120s
	-bash -c "sleep 60"   # wait for LB network interfaces to release (bash: portable on Windows)
	$(TF) init
	$(TF) destroy -auto-approve
	@echo ">>> Destroyed. Verify nothing is left behind:"
	@if [ "$(CLOUD)" = "aws" ]; then \
		echo "    aws ec2 describe-volumes --filters Name=status,Values=available"; \
		echo "    console: EKS, EC2, NAT GW, S3"; \
	else \
		echo "    gcloud compute disks list"; \
		echo "    gcloud compute forwarding-rules list"; \
	fi

kubeconfig: CLOUDS = $(CLOUDS_INFRA)
kubeconfig: check-cloud ## Point kubectl at the cluster
	@eval "$$($(TF) output -raw kubeconfig_command)"
	kubectl get nodes

## ----- Local (kind) -----

kind-up: ## Create local 3-node kind cluster (free dev loop)
	kind create cluster --config kind/cluster.yaml
	kubectl get nodes

kind-down: ## Delete the local kind cluster
	kind delete cluster --name argus

## ----- Platform -----

deploy: CLOUDS = $(CLOUDS_ALL)
deploy: check-cloud check-context ## Deploy observability, demo app, chaos tooling, Argus services
	CLOUD=$(CLOUD) bash scripts/deploy.sh

frontend-public: ## Expose the demo app via a cloud load balancer (billed; delete when done)
	kubectl -n boutique expose deployment frontend --name=frontend-external \
		--type=LoadBalancer --port=80 --target-port=8080
	@echo ">>> Address:          kubectl -n boutique get svc frontend-external -w"
	@echo ">>> Remove when done: kubectl -n boutique delete svc frontend-external"

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

grafana-password: ## Print the generated Grafana admin password
	@kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d && echo

## ----- ML -----

train: ## Train anomaly models on recent Prometheus data, register in MLflow
	kubectl -n aiops create configmap argus-training-code --from-file=ml/training/train_anomaly.py \
		--dry-run=client -o yaml | kubectl apply -f -
	kubectl -n aiops delete job argus-train-anomaly --ignore-not-found
	kubectl apply -f ml/training/train-job.yaml
	kubectl -n aiops wait --for=condition=complete --timeout=15m job/argus-train-anomaly || \
		(kubectl -n aiops logs job/argus-train-anomaly --tail=30; exit 1)
	kubectl -n aiops logs job/argus-train-anomaly --tail=5

rollback: ## Flip the production anomaly-model alias back to the previous version
	$(eval POD := $(shell kubectl -n aiops get pod -l app=anomaly-detector -o jsonpath='{.items[0].metadata.name}'))
	kubectl -n aiops cp ml/training/train_anomaly.py $(POD):/tmp/train_anomaly.py
	kubectl -n aiops exec $(POD) -- python /tmp/train_anomaly.py --rollback

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

test: ## Run the unit test suite
	python -m pytest tests/ -q

lint: ## Lint Python services
	ruff check services/ ml/ src/ tests/

fmt: ## Format Terraform
	terraform fmt -recursive terraform/
