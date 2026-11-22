"""Evaluation scorer for cloudbill-detective.
Compares runner outputs against ground truth (offline fixture ground_truth.json or live AWS region inspection)
and reports:
- Recall
- False Positives (count & rate)
- Precision
- Cost & Attribution Accuracy
- Markdown results table
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.runner import DEFAULT_RESULTS_PATH, GROUND_TRUTH_PATH, run_eval  # noqa: E402

DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "report.md"


def score_results(
    ground_truth_path: Path = GROUND_TRUTH_PATH,
    results_path: Path = DEFAULT_RESULTS_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    live: bool | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Score evaluation results against ground truth and generate a Markdown report."""
    if live is not None or not results_path.exists():
        results = run_eval(
            ground_truth_path=ground_truth_path,
            output_path=results_path,
            live=live,
            region=region,
        )
    else:
        with results_path.open("r", encoding="utf-8") as f:
            results = json.load(f)

    # Use dynamic live ground truth if embedded in results, else static ground_truth.json
    if results.get("mode") == "live" and "ground_truth" in results:
        gt = results["ground_truth"]
    else:
        with ground_truth_path.open("r", encoding="utf-8") as f:
            gt = json.load(f)

    planted_by_id: dict[str, dict[str, Any]] = {
        item["resource_id"]: item for item in gt.get("planted_issues", [])
    }
    detected_by_id: dict[str, dict[str, Any]] = {
        item["resource_id"]: item for item in results.get("find_waste", {}).get("resources", [])
    }
    who_by_id: dict[str, dict[str, Any]] = results.get("who_created", {})
    explain_by_id: dict[str, dict[str, Any]] = results.get("explain_change", {})

    planted_ids = set(planted_by_id.keys())
    detected_ids = set(detected_by_id.keys())

    true_positives = planted_ids & detected_ids
    false_negatives = planted_ids - detected_ids
    false_positives = detected_ids - planted_ids

    tp_count = len(true_positives)
    fn_count = len(false_negatives)
    fp_count = len(false_positives)
    total_planted = len(planted_ids)

    recall = (tp_count / total_planted) if total_planted > 0 else 1.0
    precision = (tp_count / (tp_count + fp_count)) if (tp_count + fp_count) > 0 else 1.0

    rows: list[dict[str, Any]] = []
    cost_matches = 0
    user_matches = 0

    for rid, expected in planted_by_id.items():
        detected = detected_by_id.get(rid)
        who = who_by_id.get(rid, {})
        explain = explain_by_id.get(rid, {})

        is_detected = detected is not None
        actual_cost = detected["estimated_monthly_cost"] if detected else 0.0
        expected_cost = expected["expected_monthly_cost"]
        cost_ok = is_detected and abs(actual_cost - expected_cost) < 0.05
        if cost_ok:
            cost_matches += 1

        actual_user = who.get("user", "unknown")
        expected_user = expected["expected_user"]
        user_ok = actual_user == expected_user
        if user_ok:
            user_matches += 1

        rows.append(
            {
                "resource_id": rid,
                "category": expected["category"],
                "status": "TP (Waste Detected)" if is_detected else "FN (Missed)",
                "expected_cost": f"${expected_cost:.2f}",
                "actual_cost": f"${actual_cost:.2f}" if is_detected else "-",
                "expected_user": expected_user,
                "actual_user": actual_user,
                "explanation_ok": "Yes"
                if (explain.get("explanation") and f"{expected_cost:.2f}" in explain["explanation"])
                else "No",
            }
        )

    for fp_id in sorted(false_positives):
        fp_item = detected_by_id[fp_id]
        rows.append(
            {
                "resource_id": fp_id,
                "category": fp_item.get("resource_type", "unknown"),
                "status": "FP (False Positive)",
                "expected_cost": "$0.00",
                "actual_cost": f"${fp_item.get('estimated_monthly_cost', 0.0):.2f}",
                "expected_user": "-",
                "actual_user": who_by_id.get(fp_id, {}).get("user", "-"),
                "explanation_ok": "-",
            }
        )

    # Also include True Negatives (healthy resources verified not to be flagged as false positives)
    healthy_list = gt.get("healthy_resources", [])
    tn_count = 0
    for h_item in healthy_list:
        if isinstance(h_item, dict):
            h_id = h_item["resource_id"]
            h_cat = h_item.get("category", "healthy_resource")
        else:
            h_id = str(h_item)
            h_cat = "healthy_resource"
        if h_id not in detected_ids:
            tn_count += 1
            if results.get("mode") == "live":
                rows.append(
                    {
                        "resource_id": h_id,
                        "category": h_cat,
                        "status": "TN (Healthy — Not Flagged)",
                        "expected_cost": "$0.00",
                        "actual_cost": "$0.00",
                        "expected_user": "-",
                        "actual_user": "-",
                        "explanation_ok": "Yes",
                    }
                )

    md_lines = [
        f"# CloudBill Detective — Evaluation Report ({results.get('mode', 'fixture').upper()} Mode, {results.get('region', 'us-east-1')})",
        "",
        "## Summary Metrics",
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Mode / Region** | `{results.get('mode', 'fixture')}` / `{results.get('region', 'us-east-1')}` |",
        f"| **Wasteful Issues (Ground Truth)** | {total_planted} |",
        f"| **Healthy Resources (True Negatives)** | {tn_count} |",
        f"| **True Positives (TP)** | {tp_count} |",
        f"| **False Negatives (FN)** | {fn_count} |",
        f"| **False Positives (FP)** | {fp_count} |",
        f"| **Recall** | {recall * 100:.1f}% ({tp_count}/{total_planted}) |",
        f"| **Precision** | {precision * 100:.1f}% ({tp_count}/{tp_count + fp_count}) |",
        f"| **Cost Estimation Accuracy** | {((cost_matches / total_planted) * 100 if total_planted else 100.0):.1f}% |",
        f"| **Creator Attribution Accuracy** | {((user_matches / total_planted) * 100 if total_planted else 100.0):.1f}% |",
        "",
        "## Detailed Results Table",
        "",
        "| Resource ID | Category | Detection Status | Expected Cost/Mo | Detected Cost/Mo | Expected Creator | Detected Creator | Plain-English Explain |",
        "| :--- | :--- | :--- | :---: | :---: | :--- | :--- | :---: |",
    ]

    for r in rows:
        md_lines.append(
            f"| `{r['resource_id']}` | `{r['category']}` | {r['status']} | "
            f"{r['expected_cost']} | {r['actual_cost']} | `{r['expected_user']}` | "
            f"`{r['actual_user']}` | {r['explanation_ok']} |"
        )

    markdown_report = "\n".join(md_lines) + "\n"
    with report_path.open("w", encoding="utf-8") as f:
        f.write(markdown_report)

    return {
        "mode": results.get("mode", "fixture"),
        "region": results.get("region", "us-east-1"),
        "total_planted": total_planted,
        "true_positives": tp_count,
        "true_negatives": tn_count,
        "false_negatives": fn_count,
        "false_positives": fp_count,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "cost_accuracy": round(cost_matches / total_planted, 4) if total_planted else 1.0,
        "attribution_accuracy": round(user_matches / total_planted, 4) if total_planted else 1.0,
        "rows": rows,
        "markdown_report": markdown_report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score cloudbill-detective evaluation results")
    parser.add_argument("--run", action="store_true", help="Run runner before scoring")
    parser.add_argument("--live", action="store_true", help="Run against live AWS")
    parser.add_argument("--region", default=None, help="AWS region to evaluate")
    args = parser.parse_args()

    live_flag: bool | None = True if args.live else (False if args.run else None)
    score = score_results(live=live_flag, region=args.region)
    print(score["markdown_report"])


if __name__ == "__main__":
    main()
