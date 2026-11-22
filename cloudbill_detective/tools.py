"""Core read-only AWS bill detective tools:
1. find_waste(region)
2. who_created(resource_id)
3. explain_change(resource_id)
4. cost_summary(days, group_by)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cloudbill_detective.aws_client import (
    PROJECT_ROOT,
    get_readonly_boto3_client,
    is_fixture_mode,
    load_fixture_json,
    parse_iso_datetime,
)
from cloudbill_detective.pricing import (
    estimate_ebs_volume_monthly_cost,
    estimate_ec2_monthly_cost,
    estimate_eip_monthly_cost,
    estimate_snapshot_monthly_cost,
)

DEFAULT_CACHE_PATH = PROJECT_ROOT / ".cache" / "cost_explorer_cache.json"
CACHE_TTL_SECONDS = 86400  # 24 hours
COST_EXPLORER_API_CALL_COST_USD = 0.01

CREATION_EVENT_NAMES = {
    "CreateVolume",
    "AllocateAddress",
    "RunInstances",
    "CreateSnapshot",
    "CopySnapshot",
    "ImportVolume",
    "ImportSnapshot",
    "CreateDomain",
    "CreateCollection",
    "CreateSecurityPolicy",
    "CreateVpcEndpoint",
    "CreateDBInstance",
    "CreateDBCluster",
    "CreateBucket",
    "CreateFunction20150331",
    "CreateCluster",
}

SERVICE_TO_EVENTSOURCES: dict[str, list[str]] = {
    "opensearch": ["es.amazonaws.com", "aoss.amazonaws.com"],
    "elastic compute cloud": ["ec2.amazonaws.com"],
    "ec2": ["ec2.amazonaws.com"],
    "elastic block store": ["ec2.amazonaws.com"],
    "ebs": ["ec2.amazonaws.com"],
    "relational database": ["rds.amazonaws.com"],
    "rds": ["rds.amazonaws.com"],
    "simple storage service": ["s3.amazonaws.com"],
    "s3": ["s3.amazonaws.com"],
    "lambda": ["lambda.amazonaws.com"],
    "virtual private cloud": ["ec2.amazonaws.com"],
    "vpc": ["ec2.amazonaws.com"],
}


def _now_utc() -> datetime:
    """Return reference UTC time (supports CLOUDBILL_REFERENCE_NOW for deterministic tests)."""
    ref = os.getenv("CLOUDBILL_REFERENCE_NOW")
    if ref:
        return parse_iso_datetime(ref)
    return datetime.now(timezone.utc)


def _get_cache_path() -> Path:
    custom = os.getenv("COST_CACHE_PATH")
    if custom:
        return Path(custom)
    return DEFAULT_CACHE_PATH


def _load_cost_cache() -> dict[str, Any]:
    cache_path = _get_cache_path()
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cost_cache(cache_data: dict[str, Any]) -> None:
    cache_path = _get_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2, sort_keys=True)


def _make_cost_cache_key(start_date: str, end_date: str, days: int, group_by: str) -> str:
    raw = f"v2_gross:{start_date}:{end_date}:{days}:{group_by.upper()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def find_waste(region: str = "us-east-1") -> dict[str, Any]:
    """Find AWS waste in a region (strictly read-only):
    - Unattached EBS volumes (status == 'available')
    - Unassociated Elastic IPs (missing AssociationId and InstanceId)
    - Running EC2 instances with 7-day average CPU < 5%
    - EBS snapshots older than 90 days

    Returns a dictionary containing waste items with resource_id, resource_type,
    reason, and estimated_monthly_cost, plus summary totals.
    """
    now = _now_utc()
    snapshot_cutoff = now - timedelta(days=90)
    cw_start = now - timedelta(days=7)

    if is_fixture_mode():
        fixture = load_fixture_json("aws_resources.json")
        region_data = fixture.get("regions", {}).get(region, fixture.get("regions", {}).get("us-east-1", {}))
        volumes = region_data.get("volumes", [])
        addresses = region_data.get("addresses", [])
        instances = region_data.get("instances", [])
        cpu_metrics_map = region_data.get("cloudwatch_cpu_by_instance", {})
        snapshots = region_data.get("snapshots", [])
    else:
        ec2 = get_readonly_boto3_client("ec2", region_name=region)
        cloudwatch = get_readonly_boto3_client("cloudwatch", region_name=region)
        api_warnings: list[str] = []

        # 1. Unattached EBS volumes (DescribeVolumes)
        try:
            vol_resp = ec2.describe_volumes(Filters=[{"Name": "status", "Values": ["available"]}])
            volumes = vol_resp.get("Volumes", [])
        except Exception as exc:
            volumes = []
            api_warnings.append(f"ec2:DescribeVolumes failed: {exc}")

        # 2. Elastic IPs (DescribeAddresses)
        try:
            addr_resp = ec2.describe_addresses()
            addresses = addr_resp.get("Addresses", [])
        except Exception as exc:
            addresses = []
            api_warnings.append(f"ec2:DescribeAddresses failed: {exc}")

        # 3. Running EC2 instances (DescribeInstances)
        instances = []
        try:
            inst_resp = ec2.describe_instances(
                Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
            )
            for reservation in inst_resp.get("Reservations", []):
                instances.extend(reservation.get("Instances", []))
        except Exception as exc:
            api_warnings.append(f"ec2:DescribeInstances failed: {exc}")

        cpu_metrics_map = {}
        for inst in instances:
            iid = inst["InstanceId"]
            try:
                metric_resp = cloudwatch.get_metric_statistics(
                    Namespace="AWS/EC2",
                    MetricName="CPUUtilization",
                    Dimensions=[{"Name": "InstanceId", "Value": iid}],
                    StartTime=cw_start,
                    EndTime=now,
                    Period=86400,
                    Statistics=["Average"],
                )
                datapoints = [dp["Average"] for dp in metric_resp.get("Datapoints", []) if "Average" in dp]
                cpu_metrics_map[iid] = datapoints
            except Exception as exc:
                cpu_metrics_map[iid] = []
                api_warnings.append(f"cloudwatch:GetMetricStatistics ({iid}) failed: {exc}")

        # 4. EBS Snapshots owned by self (DescribeSnapshots)
        try:
            snap_resp = ec2.describe_snapshots(OwnerIds=["self"])
            snapshots = snap_resp.get("Snapshots", [])
        except Exception as exc:
            snapshots = []
            api_warnings.append(f"ec2:DescribeSnapshots failed: {exc}")

    findings: list[dict[str, Any]] = []

    # Process EBS volumes
    for vol in volumes:
        state = vol.get("State") or vol.get("Status")
        if state != "available":
            continue
        vol_id = vol["VolumeId"]
        size_gb = vol.get("Size", 0)
        vol_type = vol.get("VolumeType", "gp3")
        iops = vol.get("Iops")
        monthly_cost = estimate_ebs_volume_monthly_cost(size_gb, vol_type, iops)
        findings.append(
            {
                "resource_id": vol_id,
                "resource_type": "ebs_volume",
                "region": region,
                "reason": f"Unattached EBS volume (state=available, {size_gb} GiB {vol_type})",
                "estimated_monthly_cost": monthly_cost,
                "details": {
                    "size_gb": size_gb,
                    "volume_type": vol_type,
                    "iops": iops,
                },
            }
        )

    # Process Elastic IPs
    for addr in addresses:
        if addr.get("AssociationId") or addr.get("InstanceId"):
            continue
        alloc_id = addr.get("AllocationId") or addr.get("PublicIp")
        public_ip = addr.get("PublicIp", "unknown")
        monthly_cost = estimate_eip_monthly_cost()
        findings.append(
            {
                "resource_id": alloc_id,
                "resource_type": "elastic_ip",
                "region": region,
                "reason": f"Unassociated Elastic IP ({public_ip} not attached to any instance or ENI)",
                "estimated_monthly_cost": monthly_cost,
                "details": {
                    "public_ip": public_ip,
                    "allocation_id": alloc_id,
                },
            }
        )

    # Process EC2 instances with 7-day avg CPU < 5%
    for inst in instances:
        state_name = inst.get("State", {}).get("Name", "running") if isinstance(inst.get("State"), dict) else inst.get("State", "running")
        if state_name != "running":
            continue
        iid = inst["InstanceId"]
        itype = inst.get("InstanceType", "t3.micro")
        datapoints = cpu_metrics_map.get(iid, [])
        if not datapoints:
            avg_cpu = 0.0
        else:
            avg_cpu = round(sum(datapoints) / len(datapoints), 2)
        if avg_cpu < 5.0:
            monthly_cost = estimate_ec2_monthly_cost(itype)
            findings.append(
                {
                    "resource_id": iid,
                    "resource_type": "ec2_instance",
                    "region": region,
                    "reason": f"Underutilized EC2 instance ({itype}, 7-day avg CPU {avg_cpu}% < 5.0% threshold)",
                    "estimated_monthly_cost": monthly_cost,
                    "details": {
                        "instance_type": itype,
                        "avg_cpu_7d_percent": avg_cpu,
                        "datapoints_count": len(datapoints),
                    },
                }
            )

    # Process Snapshots older than 90 days
    for snap in snapshots:
        snap_id = snap["SnapshotId"]
        start_time_raw = snap.get("StartTime")
        if not start_time_raw:
            continue
        start_dt = parse_iso_datetime(start_time_raw)
        if start_dt < snapshot_cutoff:
            age_days = (now - start_dt).days
            vol_size = snap.get("VolumeSize", 0)
            monthly_cost = estimate_snapshot_monthly_cost(vol_size)
            findings.append(
                {
                    "resource_id": snap_id,
                    "resource_type": "ebs_snapshot",
                    "region": region,
                    "reason": f"Stale EBS snapshot older than 90 days ({age_days} days old, {vol_size} GiB)",
                    "estimated_monthly_cost": monthly_cost,
                    "details": {
                        "age_days": age_days,
                        "volume_size_gb": vol_size,
                        "created_at": start_dt.isoformat(),
                    },
                }
            )

    total_cost = round(sum(item["estimated_monthly_cost"] for item in findings), 2)
    result: dict[str, Any] = {
        "region": region,
        "mode": "fixture" if is_fixture_mode() else "live",
        "waste_count": len(findings),
        "total_estimated_monthly_cost": total_cost,
        "resources": findings,
    }
    if not is_fixture_mode() and api_warnings:
        result["warnings"] = api_warnings
    return result


def _discover_live_service_events(resource_or_service: str, region: str) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Search CloudTrail across active regions by ResourceName, ResourceType, or EventSource
    (e.g., when given 'Amazon OpenSearch Service' or a domain/collection name), and also
    list OpenSearch domains/collections via read-only APIs if applicable.
    """
    q_lower = resource_or_service.lower().strip()
    matched_sources: list[str] = []
    for kw, sources in SERVICE_TO_EVENTSOURCES.items():
        if kw in q_lower:
            matched_sources.extend(sources)

    # Determine candidate regions: requested region + any regions with positive spend in Cost Explorer
    candidate_regions = [region]
    try:
        reg_spend = cost_summary(days=30, group_by="REGION")
        for item in reg_spend.get("breakdown", []):
            r_name = item.get("group", "")
            if r_name and r_name not in ("NoRegion", "global") and r_name not in candidate_regions:
                candidate_regions.append(r_name)
    except Exception:
        pass
    for fallback_reg in ("ap-southeast-2", "us-east-1", "ap-southeast-1"):
        if fallback_reg not in candidate_regions:
            candidate_regions.append(fallback_reg)

    discovered_resources: list[str] = []
    raw_events: list[dict[str, Any]] = []
    resolved_region = region

    for reg in candidate_regions[:4]:
        # If querying OpenSearch, try read-only list_domain_names / list_collections to find exact resource names
        if "opensearch" in q_lower or "es.amazonaws.com" in matched_sources:
            try:
                os_client = get_readonly_boto3_client("opensearch", region_name=reg)
                domains = [d["DomainName"] for d in os_client.list_domain_names().get("DomainNames", []) if "DomainName" in d]
                for dname in domains:
                    discovered_resources.append(f"OpenSearch Domain '{dname}' ({reg})")
            except Exception:
                pass
            try:
                aoss_client = get_readonly_boto3_client("opensearchserverless", region_name=reg)
                cols = aoss_client.list_collections().get("collectionSummaries", [])
                for c in cols:
                    discovered_resources.append(f"OpenSearch Serverless Collection '{c.get('name')} / {c.get('id')}' ({reg})")
            except Exception:
                pass

        try:
            cloudtrail = get_readonly_boto3_client("cloudtrail", region_name=reg)
            # 1. Try direct ResourceName lookup first if not a generic service name
            if not matched_sources:
                resp = cloudtrail.lookup_events(
                    LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": resource_or_service}],
                    MaxResults=50,
                )
                evs = resp.get("Events", [])
                if evs:
                    return evs, reg, discovered_resources

            # 2. If it's a service name (like "Amazon OpenSearch Service") or ResourceName had 0 hits, search by EventSource
            sources_to_check = matched_sources if matched_sources else ["es.amazonaws.com", "aoss.amazonaws.com"]
            for src in sources_to_check:
                resp = cloudtrail.lookup_events(
                    LookupAttributes=[{"AttributeKey": "EventSource", "AttributeValue": src}],
                    MaxResults=50,
                )
                evs = resp.get("Events", [])
                # Filter to non-ReadOnly management events (or creation events) if possible
                write_evs = [
                    e for e in evs
                    if e.get("EventName") in CREATION_EVENT_NAMES
                    or not str(e.get("EventName", "")).startswith(("Describe", "List", "Get"))
                ]
                if write_evs:
                    raw_events.extend(write_evs)
                    resolved_region = reg
                elif evs and not raw_events:
                    raw_events.extend(evs)
                    resolved_region = reg

            if raw_events:
                return raw_events, resolved_region, discovered_resources
        except Exception:
            continue

    return raw_events, resolved_region, discovered_resources


def who_created(resource_id: str, region: str = "us-east-1") -> dict[str, Any]:
    """Use CloudTrail LookupEvents to find the creating event, user, and time for a resource_id or AWS service name."""
    discovered_resources: list[str] = []
    resolved_region = region

    if is_fixture_mode():
        fixture = load_fixture_json("aws_resources.json")
        events_by_resource = fixture.get("cloudtrail_events", {})
        raw_events = events_by_resource.get(resource_id, [])
    else:
        try:
            raw_events, resolved_region, discovered_resources = _discover_live_service_events(resource_id, region)
        except Exception as exc:
            err_str = str(exc)
            short_user = "AccessDenied (Needs CloudTrail ReadOnly)" if "AccessDenied" in err_str else "Error querying CloudTrail"
            return {
                "resource_id": resource_id,
                "found": False,
                "event_name": "CloudTrailError",
                "user": short_user,
                "event_time": None,
                "error": err_str,
                "message": f"CloudTrail LookupEvents failed for '{resource_id}': {err_str}",
            }

    if not raw_events:
        disc_note = f" Active resources found: {', '.join(discovered_resources)}." if discovered_resources else ""
        return {
            "resource_id": resource_id,
            "found": False,
            "event_name": "NotFoundInCloudTrail",
            "user": "Not found in CloudTrail (90d history)",
            "event_time": None,
            "discovered_resources": discovered_resources,
            "message": (
                f"No CloudTrail creation event found for '{resource_id}' in {resolved_region}.{disc_note} "
                f"(Note: ensure IAM user has AWSCloudTrail_ReadOnlyAccess and check the region where the resource was created.)"
            ),
        }

    # Normalize events and prefer explicit creation events, sorted earliest first
    normalized: list[dict[str, Any]] = []
    for ev in raw_events:
        event_name = ev.get("EventName", "UnknownEvent")
        username = ev.get("Username")
        event_time_raw = ev.get("EventTime")
        event_time_dt = parse_iso_datetime(event_time_raw) if event_time_raw else None
        cloudtrail_detail: dict[str, Any] = {}
        raw_ct = ev.get("CloudTrailEvent")
        if isinstance(raw_ct, str):
            try:
                cloudtrail_detail = json.loads(raw_ct)
            except json.JSONDecodeError:
                cloudtrail_detail = {}
        elif isinstance(raw_ct, dict):
            cloudtrail_detail = raw_ct

        if not username:
            user_identity = cloudtrail_detail.get("userIdentity", {})
            username = (
                user_identity.get("userName")
                or user_identity.get("sessionContext", {}).get("sessionIssuer", {}).get("userName")
                or user_identity.get("arn")
                or user_identity.get("principalId")
                or "unknown"
            )

        # Extract any resource names referenced in the CloudTrail event
        ev_resources = [
            r.get("ResourceName")
            for r in ev.get("Resources", [])
            if r.get("ResourceName")
        ]

        normalized.append(
            {
                "event_id": ev.get("EventId") or cloudtrail_detail.get("eventID"),
                "event_name": event_name,
                "user": username,
                "event_time": event_time_dt.isoformat() if event_time_dt else str(event_time_raw),
                "_dt": event_time_dt or datetime.max.replace(tzinfo=timezone.utc),
                "source_ip": cloudtrail_detail.get("sourceIPAddress"),
                "user_agent": cloudtrail_detail.get("userAgent"),
                "resources": ev_resources,
            }
        )

    creation_matches = [e for e in normalized if e["event_name"] in CREATION_EVENT_NAMES]
    candidates = creation_matches if creation_matches else normalized
    candidates.sort(key=lambda e: e["_dt"])
    chosen = candidates[0]
    actual_resource = (chosen["resources"][0] if chosen.get("resources") else None) or (
        discovered_resources[0] if discovered_resources else resource_id
    )

    return {
        "resource_id": actual_resource,
        "queried_id": resource_id,
        "region": resolved_region,
        "found": True,
        "event_name": chosen["event_name"],
        "user": chosen["user"],
        "event_time": chosen["event_time"],
        "event_id": chosen["event_id"],
        "source_ip": chosen["source_ip"],
        "user_agent": chosen["user_agent"],
        "discovered_resources": discovered_resources or chosen.get("resources", []),
    }


def explain_change(resource_id: str, region: str = "us-east-1") -> dict[str, Any]:
    """Combine find_waste and who_created into a short plain-English explanation
    with estimated monthly cost for a given resource_id.
    """
    waste_report = find_waste(region=region)
    matching_waste = next(
        (item for item in waste_report.get("resources", []) if item["resource_id"] == resource_id),
        None,
    )
    creator_info = who_created(resource_id=resource_id, region=region)

    if matching_waste:
        cost = matching_waste["estimated_monthly_cost"]
        reason = matching_waste["reason"]
        if creator_info.get("found"):
            explanation = (
                f"Resource {resource_id} adds an estimated ${cost:.2f}/month to your AWS bill because it is "
                f"flagged as waste: {reason}. According to CloudTrail, it was created by '{creator_info['user']}' "
                f"via {creator_info['event_name']} on {creator_info['event_time']}."
            )
        else:
            explanation = (
                f"Resource {resource_id} adds an estimated ${cost:.2f}/month to your AWS bill because it is "
                f"flagged as waste: {reason}. CloudTrail had no creation event record for this resource."
            )
        return {
            "resource_id": resource_id,
            "region": region,
            "is_waste": True,
            "estimated_monthly_cost": cost,
            "reason": reason,
            "created_by": creator_info.get("user"),
            "creating_event": creator_info.get("event_name"),
            "created_at": creator_info.get("event_time"),
            "explanation": explanation,
            "waste_finding": matching_waste,
            "attribution": creator_info,
        }

    # Check if resource_id matches a service in Cost Explorer (e.g. "Amazon OpenSearch Service")
    matched_service_cost = 0.0
    matched_service_name = None
    try:
        ce_summary = cost_summary(days=30, group_by="SERVICE")
        for item in ce_summary.get("breakdown", []):
            if resource_id.lower() in item["group"].lower() or item["group"].lower() in resource_id.lower():
                matched_service_cost = item["cost_usd"]
                matched_service_name = item["group"]
                break
    except Exception:
        pass

    if matched_service_name and matched_service_cost > 0:
        disc_str = (
            f" Discovered resources: {', '.join(creator_info['discovered_resources'])}."
            if creator_info.get("discovered_resources")
            else ""
        )
        if creator_info.get("found"):
            explanation = (
                f"{matched_service_name} generated ${matched_service_cost:.2f} in AWS spend over the last 30 days. "
                f"CloudTrail shows resource '{creator_info['resource_id']}' in {creator_info.get('region', region)} "
                f"was created/modified by '{creator_info['user']}' via {creator_info['event_name']} "
                f"on {creator_info['event_time']}.{disc_str}"
            )
        else:
            explanation = (
                f"{matched_service_name} generated ${matched_service_cost:.2f} in AWS spend over the last 30 days.{disc_str} "
                f"{creator_info.get('message', '')}"
            )
        return {
            "resource_id": creator_info.get("resource_id", resource_id),
            "region": creator_info.get("region", region),
            "is_waste": True,
            "estimated_monthly_cost": matched_service_cost,
            "reason": f"Active 30-day spend on {matched_service_name} (${matched_service_cost:.2f})",
            "created_by": creator_info.get("user"),
            "creating_event": creator_info.get("event_name"),
            "created_at": creator_info.get("event_time"),
            "explanation": explanation,
            "waste_finding": None,
            "attribution": creator_info,
        }

    # Not flagged as waste
    if creator_info.get("error"):
        explanation = (
            f"Resource {resource_id} is not flagged as waste in {region} ($0.00/month estimated waste). "
            f"However, CloudTrail attribution failed: {creator_info['error']}"
        )
    elif creator_info.get("found"):
        explanation = (
            f"Resource {resource_id} is not currently flagged as waste in {region} ($0.00/month estimated waste). "
            f"It was originally created by '{creator_info['user']}' via {creator_info['event_name']} "
            f"on {creator_info['event_time']}."
        )
    else:
        explanation = (
            f"Resource {resource_id} was not found among wasteful resources in {region} ($0.00/month estimated waste). "
            f"{creator_info.get('message', 'No matching creation event was found in CloudTrail.')}"
        )

    return {
        "resource_id": resource_id,
        "region": region,
        "is_waste": False,
        "estimated_monthly_cost": 0.0,
        "reason": "Resource is active/healthy or not present in waste scan",
        "created_by": creator_info.get("user"),
        "creating_event": creator_info.get("event_name"),
        "created_at": creator_info.get("event_time"),
        "explanation": explanation,
        "waste_finding": None,
        "attribution": creator_info,
    }


def cost_summary(days: int = 30, group_by: str = "SERVICE", force_refresh: bool = False) -> dict[str, Any]:
    """Summarize AWS spend over the given number of days grouped by dimension (e.g. SERVICE, REGION, USAGE_TYPE).
    - Excludes negative Credit/Refund lines from the service breakdown so credit-backed trial usage (e.g. $21.36) is visible.
    - Uses local disk cache to avoid repeated $0.01 Cost Explorer API charges.
    - When COST_MODE=fixture, reads directly from tests/fixtures/cost_explorer.json instead of calling AWS.
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    normalized_group = group_by.strip().upper()
    now = _now_utc().date()
    start_date = (now - timedelta(days=days)).isoformat()
    # AWS Cost Explorer End date is exclusive; use tomorrow in live mode to include today's estimated usage
    end_date = now.isoformat() if is_fixture_mode() else (now + timedelta(days=1)).isoformat()

    if is_fixture_mode():
        fixture = load_fixture_json("cost_explorer.json")
        raw_results_by_time = fixture.get("by_dimension", {}).get(
            normalized_group,
            fixture.get("by_dimension", {}).get("SERVICE", []),
        )
        return _format_cost_explorer_output(
            results_by_time=raw_results_by_time,
            days=days,
            group_by=normalized_group,
            start_date=start_date,
            end_date=end_date,
            source="fixture",
            cache_hit=False,
            api_call_cost_incurred_usd=0.0,
            api_cost_saved_usd=COST_EXPLORER_API_CALL_COST_USD,
            credits_applied_usd=0.0,
        )

    # Check local cache first (each Cost Explorer API call costs $0.01)
    cache_key = _make_cost_cache_key(start_date, end_date, days, normalized_group)
    cache = _load_cost_cache()
    cached_entry = None if force_refresh else cache.get(cache_key)
    current_epoch = time.time()

    if cached_entry and (current_epoch - cached_entry.get("cached_at_epoch", 0)) < CACHE_TTL_SECONDS:
        return _format_cost_explorer_output(
            results_by_time=cached_entry["results_by_time"],
            days=days,
            group_by=normalized_group,
            start_date=start_date,
            end_date=end_date,
            source="cache",
            cache_hit=True,
            api_call_cost_incurred_usd=0.0,
            api_cost_saved_usd=COST_EXPLORER_API_CALL_COST_USD,
            credits_applied_usd=cached_entry.get("credits_applied_usd", 0.0),
        )

    # Live read-only call to AWS Cost Explorer (GetCostAndUsage)
    # Exclude RECORD_TYPE=Credit/Refund so trial/promotional credits don't cancel out service usage to $0.00
    ce = get_readonly_boto3_client("ce", region_name="us-east-1")
    try:
        usage_filter: dict[str, Any] = {
            "Not": {
                "Dimensions": {
                    "Key": "RECORD_TYPE",
                    "Values": ["Credit", "Refund"],
                }
            }
        }
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="DAILY" if days <= 14 else "MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": normalized_group}],
            Filter=usage_filter,
        )
        results_by_time = response.get("ResultsByTime", [])

        # Also check how much credit was applied in this window
        credits_applied_usd = 0.0
        try:
            credit_resp = ce.get_cost_and_usage(
                TimePeriod={"Start": start_date, "End": end_date},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                Filter={
                    "Dimensions": {
                        "Key": "RECORD_TYPE",
                        "Values": ["Credit"],
                    }
                },
            )
            for p in credit_resp.get("ResultsByTime", []):
                amt = float(p.get("Total", {}).get("UnblendedCost", {}).get("Amount", "0.0"))
                credits_applied_usd += abs(amt)
            credits_applied_usd = round(credits_applied_usd, 2)
        except Exception:
            credits_applied_usd = 0.0
    except Exception as exc:
        return {
            "days": days,
            "group_by": normalized_group,
            "time_period": {"start": start_date, "end": end_date},
            "source": "aws_cost_explorer_error",
            "cache_hit": False,
            "api_call_cost_incurred_usd": 0.0,
            "api_cost_saved_usd": 0.0,
            "total_cost_usd": 0.0,
            "credits_applied_usd": 0.0,
            "net_after_credits_usd": 0.0,
            "breakdown": [],
            "periods": [],
            "error": str(exc),
        }

    # Save to local disk cache
    cache[cache_key] = {
        "cached_at_epoch": current_epoch,
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "group_by": normalized_group,
        "results_by_time": results_by_time,
        "credits_applied_usd": credits_applied_usd,
    }
    _save_cost_cache(cache)

    return _format_cost_explorer_output(
        results_by_time=results_by_time,
        days=days,
        group_by=normalized_group,
        start_date=start_date,
        end_date=end_date,
        source="aws_cost_explorer",
        cache_hit=False,
        api_call_cost_incurred_usd=COST_EXPLORER_API_CALL_COST_USD,
        api_cost_saved_usd=0.0,
        credits_applied_usd=credits_applied_usd,
    )


def _format_cost_explorer_output(
    results_by_time: list[dict[str, Any]],
    days: int,
    group_by: str,
    start_date: str,
    end_date: str,
    source: str,
    cache_hit: bool,
    api_call_cost_incurred_usd: float,
    api_cost_saved_usd: float,
    credits_applied_usd: float = 0.0,
) -> dict[str, Any]:
    """Aggregate Cost Explorer ResultsByTime into a clean summary sorted by highest spend."""
    scale_factor = 1.0
    if source == "fixture":
        # Fixtures store a 30-day baseline; scale proportionally if days != 30
        scale_factor = days / 30.0

    totals_by_group: dict[str, float] = {}
    period_breakdown: list[dict[str, Any]] = []

    for period in results_by_time:
        tp = period.get("TimePeriod", {})
        groups = period.get("Groups", [])
        period_items: dict[str, float] = {}
        for g in groups:
            key = g.get("Keys", ["Unknown"])[0]
            amount_str = g.get("Metrics", {}).get("UnblendedCost", {}).get("Amount", "0.0")
            amount = round(float(amount_str) * scale_factor, 4)
            if amount > 0:
                totals_by_group[key] = round(totals_by_group.get(key, 0.0) + amount, 4)
                period_items[key] = round(amount, 2)
        period_breakdown.append(
            {
                "start": tp.get("Start", start_date),
                "end": tp.get("End", end_date),
                "costs": period_items,
            }
        )

    ranked_groups = [
        {"group": k, "cost_usd": round(v, 2)}
        for k, v in sorted(totals_by_group.items(), key=lambda kv: kv[1], reverse=True)
        if round(v, 2) > 0
    ]
    total_spend = round(sum(item["cost_usd"] for item in ranked_groups), 2)
    net_after_credits = round(max(0.0, total_spend - credits_applied_usd), 2)

    return {
        "days": days,
        "group_by": group_by,
        "time_period": {"start": start_date, "end": end_date},
        "source": source,
        "cache_hit": cache_hit,
        "api_call_cost_incurred_usd": api_call_cost_incurred_usd,
        "api_cost_saved_usd": api_cost_saved_usd,
        "total_cost_usd": total_spend,
        "credits_applied_usd": credits_applied_usd,
        "net_after_credits_usd": net_after_credits,
        "breakdown": ranked_groups,
        "periods": period_breakdown,
    }

