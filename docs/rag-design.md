# Argus RAG — Step 1 Design: Runbook Corpus, Index and Retrieval

**Status:** Built 2026-10-10 on `feat/rag-retrieval`, verified locally; CI job not yet run ·
**Scope:** step 1 of 4 — everything up to "given an incident, return the right runbook sections,
and prove it in CI". No LLM call, no service API, no Slack.

## 1. Goal and scope

Replace the three hardcoded runbooks and keyword scoring in `src/llm_diagnostic.py` with a real
retrieval system: a reviewed corpus in git, an idempotent ingestion job, a hybrid index in
Postgres + pgvector, and a retrieval evaluation that gates changes in CI.

**Success:**
- `make rag-ingest` builds the index from `runbooks/` and is safe to re-run.
- `retrieve(incident)` takes an incident in the correlator's real shape and returns ranked
  chunks, or an explicit "no match".
- `make rag-eval` prints hit@1, hit@3 and MRR for keyword-only, vector-only and hybrid retrieval
  on a golden set, and CI fails if hybrid drops below the committed baseline.

**In scope:** corpus format and first runbooks, chunking, embeddings, schema, ingestion,
retrieval, golden set, CI job, local workflow.
**Out of scope (later steps):** LLM call and provider switch (step 2); metrics, audit table,
Helm/Argo CD deployment (step 3); correlator and Slack wiring (step 4).

`src/llm_diagnostic.py` and its tests are untouched in this step. Step 2 swaps its
`RunbookStore` for the retriever built here.

## 2. What the repo looks like today (and what that changes)

| Finding | Where | Consequence |
|---|---|---|
| There is no Postgres. MLflow runs on SQLite on a PVC; `architecture.md` describes Postgres but the chart never deployed it | `helm/platform/templates/mlflow.yaml` | pgvector means **adding** a Postgres instance, not reusing one (D1) |
| The real incident has `alerts[{alertname, service, severity, source}]`, `services`, `severity`, `probable_root_service` — no free-text signals, no dependency edges | `services/alert-correlator/main.py` | The retrieval query is built from alert names and service names, not prose (§6) |
| The real alerts are `ServiceAnomalyDetected/Critical`, `BoutiquePodRestarting`, `BoutiquePodsNotReady`, `BoutiqueMemoryNearLimit`, `CapacityExhaustionForecast/Imminent`, plus three meta alerts | `observability/rules/` | The corpus is written for these, not for the demo's "DB pool exhaustion" (§3) |
| `docs/runbooks/` holds operator documents for running Argus itself | `disaster-recovery.md`, `session-runbook.md` | The incident corpus gets its own top-level `runbooks/` so those are never indexed |
| Services are one `main.py` each, built by a shared Dockerfile that copies only that file | `services/Dockerfile` | The RAG code is several modules plus a baked model, so it gets its own Dockerfile, like the trainer (D5) |
| Nodes are 2 vCPU with 4–8 GB | `gcp-port-design.md` §3 | Embedding model must be small and CPU-only (D2) |

## 3. Corpus

**Location:** `runbooks/*.md`. One file per failure mode. Changes go through pull request; that
review is the trust boundary, so runbook text is treated as trusted input to the prompt later.

**Format:** YAML front matter plus fixed H2 sections.

```markdown
---
id: memory-near-limit
title: Container memory near limit / OOMKilled
alerts: [BoutiqueMemoryNearLimit, BoutiquePodRestarting]
services: ["*"]            # or an explicit list, e.g. [cartservice, redis-cart]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms
## Likely causes
## Diagnosis
## Remediation
## Escalation
```

**First corpus (about 10 runbooks),** tied to the alerts and chaos experiments that exist:

| id | Alerts | Chaos experiment |
|---|---|---|
| `service-cpu-saturation` | ServiceAnomalyDetected, ServiceAnomalyCritical | `cpu-stress` |
| `pod-crashloop` | BoutiquePodRestarting | `pod-kill` |
| `pods-not-ready` | BoutiquePodsNotReady | `pod-kill` |
| `memory-near-limit` | BoutiqueMemoryNearLimit, BoutiquePodRestarting | — |
| `dependency-latency` | ServiceAnomalyDetected on a caller and its dependency | `network-delay` |
| `redis-cart-unavailable` | BoutiquePodsNotReady (redis-cart), anomalies on cartservice | `pod-kill` |
| `node-capacity-exhaustion` | CapacityExhaustionForecast, CapacityExhaustionImminent | — |
| `anomaly-false-positive` | ServiceAnomalyDetected with no static alert alongside | — |
| `detector-not-scoring` | DetectorNotScoring, DetectorModelMissing | — |
| `forecaster-not-running` | ForecasterNotRunning | — |

Disk and memory exhaustion are one runbook, not two: the correlator keeps only `alertname`,
`service`, `severity` and `source` from each alert, so the `resource` label that would tell
them apart never reaches retrieval.

Several of these are deliberately confusable (`pod-crashloop` / `pods-not-ready` /
`memory-near-limit` share alerts). Without that overlap, ten runbooks are too easy to tell apart
and the evaluation would prove nothing.

**Corpus lint (runs in CI):** front matter validates against a schema; `id` matches the file
name and is unique; all five sections are present; every name in `alerts` exists in
`observability/rules/`; every named service exists in the correlator's topology;
`last_reviewed` is a date. A separate test checks the reverse: every alert rule has at least
one runbook.

## 4. Chunking and embeddings

- **Chunk = one H2 section.** Runbooks are short, so a section is roughly 100–300 tokens. A
  section over about 350 tokens is split on paragraph boundaries.
- **Contextual header:** each chunk's embedded text is prefixed with
  `"<runbook title> — <section>"`, so a "Remediation" section still says what it remediates.
- **Chunk id:** `<runbook_id>#<section-slug>[-n]`. Stable across runs, and it is the citation id
  the LLM must quote in step 2.
- **Content hash:** SHA-256 of the embedded text; ingestion re-embeds only chunks whose hash
  changed.
- **Model:** `BAAI/bge-small-en-v1.5` (384 dimensions) through `fastembed`, which runs it on
  ONNX Runtime with no PyTorch. Queries and passages use the library's separate
  `query_embed` / `passage_embed` calls, because this model family expects a query prefix.

## 5. Index

One Postgres 16 database (`argus_rag`) from the `pgvector/pgvector:pg16` image.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE runbooks (
  id            text PRIMARY KEY,
  title         text NOT NULL,
  alerts        text[] NOT NULL,
  services      text[] NOT NULL,
  severity      text,
  owner         text,
  last_reviewed date,
  source_path   text NOT NULL
);

CREATE TABLE runbook_chunks (
  id           text PRIMARY KEY,                 -- <runbook_id>#<section>
  runbook_id   text NOT NULL REFERENCES runbooks(id) ON DELETE CASCADE,
  section      text NOT NULL,
  ordinal      int  NOT NULL,
  content      text NOT NULL,                    -- the section as written
  text         text NOT NULL,                    -- what was embedded: title, section, content
  content_hash text NOT NULL,
  embedding    vector(384) NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX ON runbook_chunks USING gin (tsv);
CREATE INDEX ON runbooks USING gin (alerts);

CREATE TABLE index_meta (key text PRIMARY KEY, value text NOT NULL);
-- embedding_model, embedding_dim, corpus_git_sha, indexed_at
```

**No approximate-nearest-neighbour index.** At about 50 chunks an exact scan is faster than an
HNSW index and returns exact results. Add HNSW when the corpus reaches thousands of chunks.

**Two database roles** — `rag_ingest` (read/write) for the job, `rag_read` (select only) for
the service — are created with the in-cluster deployment in step 3. Locally and in CI both
run as the throwaway database's superuser.

## 6. Ingestion

`python -m ingest` in `services/diagnostic/`:

1. Parse and lint every file in `runbooks/`; any lint error aborts before the database is touched.
2. Chunk, build embedded text, hash.
3. Read `index_meta`. If the embedding model or dimension differs from the code's, mark every
   chunk for re-embedding.
4. Embed only new or changed chunks.
5. In **one transaction**: upsert runbooks, upsert changed chunks, delete chunks and runbooks no
   longer in the corpus, update `index_meta`. Readers never see a half-built index.
6. Print a summary: added, updated, unchanged, deleted.

Re-running with no corpus change embeds nothing and writes only `indexed_at`.

## 7. Retrieval

`retrieve(incident, k=4) -> RetrievalResult` where the result is either ranked chunks or
`no_match` with the best score seen.

**Query construction** from the correlator's incident:
- `root` = `probable_root_service`
- `alert_names` = distinct `alertname` values
- query text = `"<alertname> on <service>"` for each alert, root service first, de-duplicated.

**Three signals,** each restricted to runbooks whose `services` contains `root` or `"*"`:

| Signal | How it scores | What it catches |
|---|---|---|
| Alert agreement | Weighted Jaccard between the incident's alert names and the runbook's `alerts`. Each alert weighs by how few runbooks list it | Exact, known alerts |
| Vector | Cosine similarity of the query embedding, top 20 | Alerts no runbook lists; which section fits |
| Keyword | Postgres full-text `ts_rank`, any-term match, top 20 | Exact service and component names |

**Ranking:** alert agreement orders the result. Vector and keyword are fused with reciprocal
rank fusion (`score = Σ 1/(60 + rank)`); that fused score orders chunks with equal agreement,
and is the only ordering for an incident whose alerts no runbook lists.

This replaced the first design, which fused all three as equal ranked lists. The golden set
showed the flaw: a one-place difference in the alert ranking is worth `1/61 − 1/62`, so the
text signals overrode exact alert matches and `anomaly-false-positive` won most incidents
that included `ServiceAnomalyDetected`. On the 26 positive cases that existed at the time, hit@1
went from 0.77 to 0.88. The rarity weighting exists for the same alert: it accompanies almost every
incident, so it should say less than `BoutiquePodsNotReady`.

**No-match rule:** return `no_match` when no runbook shares an alert with the incident **and**
the best cosine similarity is under `MIN_SIMILARITY` (0.73, set from §8).

**Output:** the ranked runbook ids, and per chunk its id, runbook, section, content, alert
agreement, fused score and rank in each text signal, so a bad retrieval can be explained.

## 8. Evaluation

**Golden set:** `services/diagnostic/eval/golden.yaml`, 36 cases. Each case is the raw alerts
of one incident plus the expected runbook, a list of acceptable runbooks, or `none`.
`run_eval.py` folds the alerts through the real `alert-correlator` code, so the incident shape
and inferred root are what production would produce.

| Kind | Count | What it tests |
|---|---|---|
| `single` | 10 | One failure mode, its own alerts |
| `multi` | 11 | Several alerts folded into one incident, including the three chaos experiments |
| `confusable` | 5 | Runbooks that share alerts. Three accept two runbooks, because the incident alone cannot separate them |
| `unlisted` | 5 | kube-prometheus-stack alerts no runbook lists but one covers; only the text signals can find these |
| `negative` | 5 | Alerts with no runbook; expected `none` |

All cases are hand-written from the alert rules and chaos experiments. None is captured from
a live run yet (R3).

**Metrics (runbook level):** hit@1, hit@3, MRR on positive cases; rejection rate on negatives.

**First results** (`make rag-eval`, 2026-10-10, local):

```
mode       hit@1  hit@3    MRR  negatives rejected
alert       0.71   0.84   0.77                1.00
keyword     0.81   0.87   0.86                0.20
vector      0.65   0.87   0.74                1.00
hybrid      0.87   0.97   0.92                1.00
```

No single signal is enough: alert agreement cannot see unlisted alerts, and the text signals
cannot tell confusable runbooks apart. Known misses in hybrid:

- `unlisted-filesystem-filling` is rejected as no-match (similarity 0.676, below the threshold).
- `single-redis-not-ready` ranks the generic `pods-not-ready` above `redis-cart-unavailable`.
- `multi-crashloop-cascade` ranks `pods-not-ready` above `pod-crashloop`; both alerts fired.
- `confusable-caller-and-dependency` ranks `anomaly-false-positive` first.

**The threshold is a trade-off, not a clean cut.** Best cosine similarity was 0.600–0.716 for
negatives and 0.676–0.777 for unlisted positives: the ranges overlap. 0.73 rejects all five
negatives and gives up one unlisted positive, on the view that grounding a diagnosis in the
wrong runbook is worse than offering none. It rests on ten data points.

**Gate:** the hybrid row is compared with `eval/baseline.json`. CI fails if hit@3 or
negatives-rejected falls below it. Changing the baseline is a deliberate commit
(`run_eval.py --update-baseline`), the same idea as the alarm-rate gate on model promotion.

## 9. Code layout

```
runbooks/                         incident runbook corpus (new)
services/diagnostic/
  corpus.py                       front matter, lint, chunking, hashing
  embed.py                        fastembed wrapper; model name and dimension constants
  store.py                        schema, upsert, delete, queries
  ingest.py                       the job in §6
  retrieve.py                     query construction, ranking, no-match rule (no DB or model imports)
  schema.sql
  eval/golden.yaml, eval/baseline.json, eval/run_eval.py
  requirements.txt                fastembed, psycopg[binary], pgvector, pyyaml
  Dockerfile                      added in step 2/3; bakes the model and runbooks/
tests/test_rag_corpus.py          lint, chunk ids, hashing — no database
tests/test_rag_retrieve.py        query construction, ranking, no-match rule — fake index
```

## 10. Running it

**Local (laptop):** Postgres in Docker, everything else in the existing `.venv`.

| Target | Does |
|---|---|
| `make rag-db` | Starts `pgvector/pgvector:pg16` on localhost with a throwaway password |
| `make rag-lint` | Corpus lint only |
| `make rag-ingest` | §6 against the local database |
| `make rag-eval` | §8, prints the table, exits non-zero below baseline |

**CI:** a new `rag` job in `.github/workflows/ci.yaml` with a `pgvector/pgvector:pg16` service
container. Steps: install requirements (plus the correlator's, which the evaluation imports),
restore the cached embedding model, ingest, eval. Ingest lints first. The existing `test` job
gains the two database-free test files.

**In-cluster (designed here, deployed in step 3):** a single-replica Postgres StatefulSet in
`aiops` with a small PVC and a password Secret generated at bootstrap, as Grafana's is today;
the ingest job as a post-install/post-upgrade hook using the same image as the service, so the
index always matches the runbooks baked into the deployed commit.

## 11. Decisions

**D1 — Postgres + pgvector, as a new instance.** Rejected: Chroma (a second kind of store, no
SQL filtering or full-text search in the same query), and an index file baked into the image
(works at this size, but shows nothing about operating a store). Steps 3 and 4 need Postgres
anyway for the audit table and incident state, so this instance is reused, not thrown away.

**D2 — Embeddings run in-process, not through Ollama.** Ollama on the laptop serves generation
in step 2 only. Embeddings must be the same model at ingestion, in CI and in the cluster, and
neither CI nor the cluster can reach a laptop.

**D3 — Alert agreement ranks first; text signals break ties and cover unlisted alerts.**
Incidents are structured, and the runbook's `alerts` list is reviewed metadata, so it outranks
similarity. Changed from "three equal lists" after the first evaluation run (§7).

**D4 — Alert agreement is an ordering, not a hard filter.** A hard filter returns nothing for
an alert no runbook lists, when a semantically close runbook may still help; four of the five
`unlisted` cases are found this way.

**D5 — One `services/diagnostic/` directory and one image for both the ingest job and the
service.** The corpus version, index version and code version are then a single git SHA.

**D6 — Section-level chunks with stable ids.** Stable ids make citations checkable in step 2:
the service can reject any cited id it did not retrieve.

## 12. Risks and open items

| # | Item | Plan |
|---|---|---|
| R1 | The correlator drops alert annotations, so the query is only alert names and service names. Measured: vector-only hit@1 is 0.65, and negatives and positives overlap on similarity | Keep `summary` in the correlator's alert record (small change, step 4), then re-run §8 and re-set the threshold |
| R2 | Ten runbooks make high scores easy | Confusable runbooks and negative cases (§3, §8); report results per case kind, not only overall |
| R3 | The golden set is hand-written by the same author as the runbooks, and the ranking was changed after seeing its results | Open. Capture the multi-alert cases from `GET /incidents` during real chaos runs and add them as cases the ranking has not seen |
| R4 | First embedding-model download needs network access | Cache it in CI; bake it into the image for the cluster |
| R5 | Helm hooks under Argo CD for the ingest job are assumed to behave as post-sync hooks | Verify on kind in step 3 before relying on it |
| R6 | `architecture.md` says MLflow uses Postgres; it does not | Correct the doc when this design is accepted |
