.PHONY: help up down plan kubeconfig check-cloud check-prereqs check-context kind-up kind-down deploy deploy-gitops argocd argocd-password frontend-public load load-varied load-stop chaos-cpu chaos-podkill chaos-latency chaos-clean grafana grafana-password train rollback mlflow detector-logs forecaster-logs forecasts incidents scores demo test lint rag-lint rag-db rag-ingest rag-eval fmt

# CLOUD has no default on purpose: `make down` against the wrong cloud is the
# one mistake here that is both easy to make and expensive.
#   make up CLOUD=gcp       provision GKE + GCS
#   make up CLOUD=aws       provision EKS + S3
#   make deploy CLOUD=kind  local cluster, no cloud resources
CLOUD        ?=
AWS_PROFILE  ?= argus
CLOUDS_INFRA := aws gcp
CLOUDS_ALL   := aws gcp kind
# GCP is a staged landing zone; make up|down only ever touch its workload
# stage. Stages 0-2 are applied by hand and persist (docs/gcp-port-design.md).
TF_DIR_aws   := terraform/aws
TF_DIR_gcp   := terraform/gcp/3-apps
TF_DIR       := $(TF_DIR_$(CLOUD))
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
	@echo ">>> Stopping Argo CD (if installed) so it does not recreate what is removed next..."
	-kubectl -n argocd scale statefulset argocd-application-controller --replicas=0 --timeout=60s 2>/dev/null
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
		echo "    gcloud compute disks list --project gk-argus-nonprod-gke-apps"; \
		echo "    gcloud compute forwarding-rules list --project gk-argus-nonprod-gke-apps"; \
		echo "    gcloud compute firewall-rules list --project gk-argus-nonprod-net-host  # no gke-* rules left"; \
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
	CLOUD=$(CLOUD) TF_DIR=$(TF_DIR) bash scripts/deploy.sh

deploy-gitops: CLOUDS = $(CLOUDS_ALL)
deploy-gitops: check-cloud check-context ## Same deployment via Argo CD, from the pushed branch (REVISION=<branch> to override)
	CLOUD=$(CLOUD) TF_DIR=$(TF_DIR) bash scripts/bootstrap-argocd.sh

argocd: ## Port-forward the Argo CD UI to http://localhost:8081
	kubectl -n argocd port-forward svc/argocd-server 8081:80

argocd-password: ## Print the Argo CD initial admin password (user: admin)
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d && echo

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
	kubectl -n aiops delete job argus-train-anomaly --ignore-not-found
	@image=$$(kubectl -n aiops get cronjob argus-retrain-anomaly \
		-o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}'); \
	[ -n "$$image" ] || { echo "ERROR: retrain CronJob not found — deploy the platform first"; exit 1; }; \
	echo ">>> Training with $$image"; \
	sed "s|TRAINER_IMAGE|$$image|" ml/training/train-job.yaml | kubectl apply -f -
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

rag-lint: ## Lint the incident runbook corpus
	python services/diagnostic/corpus.py

# A throwaway local database: the password is not a secret and the data is
# rebuilt from runbooks/ by rag-ingest.
rag-db: ## Start a local Postgres + pgvector for the runbook index
	-@docker rm -f argus-rag-db >/dev/null 2>&1
	docker run -d --name argus-rag-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=argus_rag 		-p 5432:5432 pgvector/pgvector:pg16
	@until docker exec argus-rag-db pg_isready -h localhost -U postgres -d argus_rag >/dev/null 2>&1; do sleep 1; done
	@echo ">>> Postgres ready on localhost:5432"

rag-ingest: ## Build the runbook index from runbooks/ (safe to re-run)
	python services/diagnostic/ingest.py

rag-eval: ## Score runbook retrieval against the golden set; fails below baseline
	python services/diagnostic/eval/run_eval.py

fmt: ## Format Terraform
	terraform fmt -recursive terraform/
