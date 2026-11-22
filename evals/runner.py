"""Evaluation runner for cloudbill-detective.
Calls find_waste, who_created, explain_change, and cost_summary.
Supports both offline fixture mode (evals/ground_truth.json) and live AWS account mode.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cloudbill_detective.aws_client import (  # noqa: E402
    get_readonly_boto3_client,
    is_fixture_mode,
    parse_iso_datetime,
)
from cloudbill_detective.pricing import (  # noqa: E402
    estimate_ebs_volume_monthly_cost,
    estimate_ec2_monthly_cost,
    estimate_eip_monthly_cost,
    estimate_snapshot_monthly_cost,
)
from cloudbill_detective.tools import (  # noqa: E402
    _now_utc,
    cost_summary,
    explain_change,
    find_waste,
    who_created,
)

EVALS_DIR = Path(__file__).resolve().parent
GROUND_TRUTH_PATH = EVALS_DIR / "ground_truth.json"
DEFAULT_RESULTS_PATH = EVALS_DIR / "results.json"


def build_live_ground_truth(region: str) -> dict[str, Any]:
    """Inspect real resources in the live AWS account for `region` to build a live ground-truth dataset
    of both wasteful resources (planted/actual issues) and healthy resources (for false-positive verification).
    """
    now = _now_utc()
    snapshot_cutoff = now - timedelta(days=90)
    cw_start = now - timedelta(days=7)

    ec2 = get_readonly_boto3_client("ec2", region_name=region)
    cloudwatch = get_readonly_boto3_client("cloudwatch", region_name=region)

    planted_issues: list[dict[str, Any]] = []
    healthy_resources: list[dict[str, Any]] = []

    # 1. Inspect ALL EBS volumes in the region
    try:
        all_vols = ec2.describe_volumes().get("Volumes", [])
        for v in all_vols:
            vid = v["VolumeId"]
            state = v.get("State") or v.get("Status")
            size_gb = v.get("Size", 0)
            vtype = v.get("VolumeType", "gp3")
            iops = v.get("Iops")
            if state == "available":
                creator = who_created(vid, region=region)
                planted_issues.append(
                    {
                        "resource_id": vid,
                        "resource_type": "ebs_volume",
                        "category": "unattached_ebs_volume",
                        "expected_monthly_cost": estimate_ebs_volume_monthly_cost(size_gb, vtype, iops),
                        "expected_user": creator.get("user", "unknown"),
                        "expected_event": creator.get("event_name"),
                        "expected_time": creator.get("event_time"),
                    }
                )
            else:
                healthy_resources.append(
                    {
                        "resource_id": vid,
                        "category": f"healthy_ebs_volume ({state}, {size_gb} GiB)",
                    }
                )
    except Exception:
        pass

    # 2. Inspect ALL Elastic IPs in the region
    try:
        all_addrs = ec2.describe_addresses().get("Addresses", [])
        for a in all_addrs:
            aid = a.get("AllocationId") or a.get("PublicIp", "unknown")
            if not a.get("AssociationId") and not a.get("InstanceId"):
                creator = who_created(aid, region=region)
                planted_issues.append(
                    {
                        "resource_id": aid,
                        "resource_type": "elastic_ip",
                        "category": "unassociated_elastic_ip",
                        "expected_monthly_cost": estimate_eip_monthly_cost(),
                        "expected_user": creator.get("user", "unknown"),
                        "expected_event": creator.get("event_name"),
                        "expected_time": creator.get("event_time"),
                    }
                )
            else:
                healthy_resources.append(
                    {
                        "resource_id": aid,
                        "category": "healthy_elastic_ip (associated)",
                    }
                )
    except Exception:
        pass

    # 3. Inspect ALL EC2 instances in the region
    try:
        res_list = ec2.describe_instances().get("Reservations", [])
        for r in res_list:
            for inst in r.get("Instances", []):
                iid = inst["InstanceId"]
                itype = inst.get("InstanceType", "t3.micro")
                state_name = inst.get("State", {}).get("Name", "unknown")
                if state_name == "terminated":
                    continue
                if state_name == "running":
                    try:
                        m = cloudwatch.get_metric_statistics(
                            Namespace="AWS/EC2",
                            MetricName="CPUUtilization",
                            Dimensions=[{"Name": "InstanceId", "Value": iid}],
                            StartTime=cw_start,
                            EndTime=now,
                            Period=86400,
                            Statistics=["Average"],
                        )
                        dps = [dp["Average"] for dp in m.get("Datapoints", []) if "Average" in dp]
                        avg_cpu = round(sum(dps) / len(dps), 2) if dps else 0.0
                    except Exception:
                        avg_cpu = 0.0

                    if avg_cpu < 5.0:
                        creator = who_created(iid, region=region)
                        planted_issues.append(
                            {
                                "resource_id": iid,
                                "resource_type": "ec2_instance",
                                "category": "low_cpu_ec2_instance",
                                "expected_monthly_cost": estimate_ec2_monthly_cost(itype),
                                "expected_user": creator.get("user", "unknown"),
                                "expected_event": creator.get("event_name"),
                                "expected_time": creator.get("event_time"),
                            }
                        )
                    else:
                        healthy_resources.append(
                            {
                                "resource_id": iid,
                                "category": f"healthy_ec2_instance (running, {avg_cpu}% avg CPU)",
                            }
                        )
                else:
                    healthy_resources.append(
                        {
                            "resource_id": iid,
                            "category": f"healthy_ec2_instance ({state_name}, {itype})",
                        }
                    )
    except Exception:
        pass

    # 4. Inspect self-owned EBS Snapshots in the region
    try:
        all_snaps = ec2.describe_snapshots(OwnerIds=["self"]).get("Snapshots", [])
        for s in all_snaps:
            sid = s["SnapshotId"]
            st = s.get("StartTime")
            vsize = s.get("VolumeSize", 0)
            if st and parse_iso_datetime(st) < snapshot_cutoff:
                creator = who_created(sid, region=region)
                planted_issues.append(
                    {
                        "resource_id": sid,
                        "resource_type": "ebs_snapshot",
                        "category": "stale_snapshot_over_90d",
                        "expected_monthly_cost": estimate_snapshot_monthly_cost(vsize),
                        "expected_user": creator.get("user", "unknown"),
                        "expected_event": creator.get("event_name"),
                        "expected_time": creator.get("event_time"),
                    }
                )
            else:
                healthy_resources.append(
                    {
                        "resource_id": sid,
                        "category": f"healthy_ebs_snapshot (<90d old, {vsize} GiB)",
                    }
                )
    except Exception:
        pass

    return {
        "region": region,
        "mode": "live",
        "planted_issues": planted_issues,
        "healthy_resources": healthy_resources,
    }


def run_eval(
    ground_truth_path: Path = GROUND_TRUTH_PATH,
    output_path: Path = DEFAULT_RESULTS_PATH,
    live: bool | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Execute all 4 MCP tools against either fixture ground truth or live AWS account."""
    if live is None:
        live = not is_fixture_mode()

    prev_cost_mode = os.environ.get("COST_MODE")
    prev_ref_now = os.environ.get("CLOUDBILL_REFERENCE_NOW")

    try:
        if not live:
            os.environ["COST_MODE"] = "fixture"
            with ground_truth_path.open("r", encoding="utf-8") as f:
                gt = json.load(f)
            if "reference_now" in gt:
                os.environ["CLOUDBILL_REFERENCE_NOW"] = gt["reference_now"]
            target_region = region or gt.get("region", "us-east-1")
        else:
            os.environ["COST_MODE"] = "live"
            os.environ.pop("CLOUDBILL_REFERENCE_NOW", None)
            target_region = region or os.getenv("AWS_REGION", "us-east-1")
            gt = build_live_ground_truth(region=target_region)

        waste_output = find_waste(region=target_region)

        all_resource_ids = list(
            dict.fromkeys(
                [item["resource_id"] for item in gt.get("planted_issues", [])]
                + [item["resource_id"] for item in waste_output.get("resources", [])]
            )
        )

        who_created_outputs: dict[str, Any] = {}
        explain_change_outputs: dict[str, Any] = {}
        for rid in all_resource_ids:
            who_created_outputs[rid] = who_created(resource_id=rid, region=target_region)
            explain_change_outputs[rid] = explain_change(resource_id=rid, region=target_region)

        cost_summary_output = cost_summary(days=30, group_by="SERVICE")

        results = {
            "region": target_region,
            "mode": "live" if live else "fixture",
            "ground_truth": gt,
            "find_waste": waste_output,
            "who_created": who_created_outputs,
            "explain_change": explain_change_outputs,
            "cost_summary": cost_summary_output,
        }

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        return results
    finally:
        if prev_cost_mode is None:
            os.environ.pop("COST_MODE", None)
        else:
            os.environ["COST_MODE"] = prev_cost_mode
        if prev_ref_now is None:
            os.environ.pop("CLOUDBILL_REFERENCE_NOW", None)
        else:
            os.environ["CLOUDBILL_REFERENCE_NOW"] = prev_ref_now


def main() -> None:
    parser = argparse.ArgumentParser(description="Run cloudbill-detective evaluation suite")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run against live AWS instead of COST_MODE=fixture",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region to evaluate (default: us-east-1 or active region)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RESULTS_PATH,
        help="Path to write evaluation results JSON",
    )
    args = parser.parse_args()
    results = run_eval(output_path=args.output, live=args.live, region=args.region)
    print(
        f"Eval runner completed ({results['mode']} mode, region={results['region']}): "
        f"detected {results['find_waste']['waste_count']} waste items "
        f"(${results['find_waste']['total_estimated_monthly_cost']:.2f}/mo). "
        f"Saved to {args.output}"
    )


if __name__ == "__main__":
    main()
