"""Score runbook retrieval against the golden set.

    python services/diagnostic/eval/run_eval.py                    # table + gate
    python services/diagnostic/eval/run_eval.py --verbose          # every case
    python services/diagnostic/eval/run_eval.py --update-baseline  # accept current scores

Each retrieval signal is scored alone and then all together, so the table
shows what fusing them buys. Only the hybrid row is gated: the run fails if it
scores below eval/baseline.json.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import embed  # noqa: E402
import retrieve  # noqa: E402
import store  # noqa: E402

GOLDEN = HERE / "golden.yaml"
BASELINE = HERE / "baseline.json"
MODES = {
    "alert": ("alert",),
    "keyword": ("keyword",),
    "vector": ("vector",),
    "hybrid": retrieve.SIGNALS,
}
GATED = ("hit@3", "negatives_rejected")


def _correlator():
    path = HERE.parents[1] / "alert-correlator" / "main.py"
    spec = importlib.util.spec_from_file_location("correlator_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.log.setLevel("WARNING")  # one line per folded alert otherwise
    return module


def to_incident(correlator, alerts: list[dict]) -> dict:
    """Fold a case's alerts exactly as the correlator would in production."""
    correlator._incidents.clear()
    for alert in alerts:
        labels = {k: v for k, v in alert.items() if k in ("alertname", "service", "severity")}
        correlator._fold_alert({"status": "firing", "labels": labels})
    assert len(correlator._incidents) == 1, "a golden case must fold into one incident"
    return correlator.incidents()[0]


def score(cases: list[dict], results: dict[str, retrieve.Result]) -> dict[str, float]:
    positives = [c for c in cases if c["expect"] != "none"]
    negatives = [c for c in cases if c["expect"] == "none"]
    hit1 = hit3 = reciprocal = 0.0
    for case in positives:
        accepted = [case["expect"]] if isinstance(case["expect"], str) else case["expect"]
        ranked = results[case["id"]].runbooks
        rank = next((i for i, rb in enumerate(ranked, start=1) if rb in accepted), None)
        if rank:
            hit1 += rank == 1
            hit3 += rank <= 3
            reciprocal += 1 / rank
    rejected = sum(1 for c in negatives if not results[c["id"]].matched)
    return {
        "hit@1": round(hit1 / len(positives), 3),
        "hit@3": round(hit3 / len(positives), 3),
        "mrr": round(reciprocal / len(positives), 3),
        "negatives_rejected": round(rejected / len(negatives), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="print every case for the hybrid mode")
    parser.add_argument("--update-baseline", action="store_true", help="write the hybrid scores to baseline.json")
    args = parser.parse_args()

    cases = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    correlator = _correlator()
    incidents = {c["id"]: to_incident(correlator, c["alerts"]) for c in cases}
    index = store.PgIndex(store.connect(), embed.embed_query)

    scores = {}
    by_mode = {}
    for mode, signals in MODES.items():
        by_mode[mode] = {cid: retrieve.retrieve(inc, index, signals=signals) for cid, inc in incidents.items()}
        scores[mode] = score(cases, by_mode[mode])

    print(f"{len(cases)} cases, min_similarity={retrieve.MIN_SIMILARITY}\n")
    print(f"{'mode':<9}{'hit@1':>7}{'hit@3':>7}{'MRR':>7}{'negatives rejected':>20}")
    for mode, s in scores.items():
        print(f"{mode:<9}{s['hit@1']:>7.2f}{s['hit@3']:>7.2f}{s['mrr']:>7.2f}{s['negatives_rejected']:>20.2f}")

    hybrid = by_mode["hybrid"]
    print("\nhybrid, by kind of case:")
    for kind in dict.fromkeys(c["kind"] for c in cases):
        subset = [c for c in cases if c["kind"] == kind]
        if kind == "negative":
            print(f"  {kind:<11}{sum(not hybrid[c['id']].matched for c in subset)}/{len(subset)} rejected")
        else:
            s = score([*subset, {"id": "_", "expect": "none"}], {**hybrid, "_": retrieve.Result(matched=False)})
            print(f"  {kind:<11}hit@1 {s['hit@1']:.2f}  hit@3 {s['hit@3']:.2f}  ({len(subset)} cases)")

    # The similarities the no-match threshold has to separate.
    vector = by_mode["vector"]
    sims = {k: sorted(vector[c["id"]].best_similarity for c in cases if (c["expect"] == "none") == k)
            for k in (True, False)}
    print(f"\nbest cosine similarity: negatives {sims[True][0]:.3f}..{sims[True][-1]:.3f}, "
          f"positives {sims[False][0]:.3f}..{sims[False][-1]:.3f}")

    if args.verbose:
        print()
        for case in cases:
            result = hybrid[case["id"]]
            got = ", ".join(result.runbooks[:3]) if result.matched else "no match"
            print(f"  {case['id']:<34} root={incidents[case['id']]['probable_root_service']:<28} "
                  f"sim={result.best_similarity:.3f}  expect={case['expect']}  got={got}")

    if args.update_baseline:
        BASELINE.write_text(json.dumps(scores["hybrid"], indent=2) + "\n", encoding="utf-8")
        print(f"\nbaseline updated: {BASELINE.name}")
        return 0
    if not BASELINE.exists():
        print("\nno baseline yet; run with --update-baseline to record one")
        return 1
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    failed = [m for m in GATED if scores["hybrid"][m] < baseline[m]]
    for metric in failed:
        print(f"\nFAIL: hybrid {metric} {scores['hybrid'][metric]} is below the baseline {baseline[metric]}")
    if not failed:
        print("\nOK: hybrid is at or above the baseline")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
