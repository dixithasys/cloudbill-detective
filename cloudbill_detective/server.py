"""cloudbill-detective MCP & FastAPI Server.
Supports:
- stdio transport (for Claude Desktop / MCP clients)
- HTTP transport (Built-in Web Dashboard UI at /, FastAPI REST endpoints at /tools/*, and MCP over SSE at /mcp)
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]

from cloudbill_detective.aws_client import (
    clear_runtime_aws_config,
    get_aws_connection_status,
    set_runtime_aws_config,
)
from cloudbill_detective.llm_agent import (
    get_llm_status,
    set_llm_config,
    try_llm_agent,
)
from cloudbill_detective.tools import (
    cost_summary as run_cost_summary,
    explain_change as run_explain_change,
    find_waste as run_find_waste,
    who_created as run_who_created,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 1. Official MCP Python SDK (FastMCP) Server
mcp = FastMCP(
    "cloudbill-detective",
    instructions=(
        "Strictly read-only AWS cost & waste detective. Explains why an AWS bill grew "
        "by finding unattached EBS volumes, unassociated Elastic IPs, low-CPU EC2 instances, "
        "and stale snapshots, attributing creators via CloudTrail, and summarizing Cost Explorer spend."
    ),
)


@mcp.tool()
def find_waste(region: str = "us-east-1") -> dict[str, Any]:
    """Scan an AWS region for wasted spend:
    - Unattached EBS volumes
    - Unassociated Elastic IPs
    - Running EC2 instances with 7-day avg CPU < 5%
    - EBS snapshots older than 90 days
    Returns resource_id, reason, and estimated_monthly_cost for each item.
    """
    return run_find_waste(region=region)


@mcp.tool()
def who_created(resource_id: str, region: str = "us-east-1") -> dict[str, Any]:
    """Use CloudTrail LookupEvents to find the creating event, user, and timestamp for an AWS resource_id."""
    return run_who_created(resource_id=resource_id, region=region)


@mcp.tool()
def explain_change(resource_id: str, region: str = "us-east-1") -> dict[str, Any]:
    """Combine waste detection and CloudTrail attribution into a short plain-English explanation with estimated monthly cost."""
    return run_explain_change(resource_id=resource_id, region=region)


@mcp.tool()
def cost_summary(days: int = 30, group_by: str = "SERVICE") -> dict[str, Any]:
    """Query AWS Cost Explorer with a local cache (each API call costs $0.01) or fixture data when COST_MODE=fixture."""
    return run_cost_summary(days=days, group_by=group_by)


# 2. FastAPI HTTP Application (serves Web Dashboard UI, REST endpoints, and mounts MCP SSE)
app = FastAPI(
    title="cloudbill-detective",
    version="0.1.0",
    description="Strictly read-only AWS bill detective MCP & HTTP server with built-in Web Dashboard",
)


class FindWasteRequest(BaseModel):
    region: str = "us-east-1"


class WhoCreatedRequest(BaseModel):
    resource_id: str
    region: str = "us-east-1"


class ExplainChangeRequest(BaseModel):
    resource_id: str
    region: str = "us-east-1"


class CostSummaryRequest(BaseModel):
    days: int = 30
    group_by: str = "SERVICE"


class AskDetectiveRequest(BaseModel):
    query: str
    region: str = "us-east-1"
    history: list[dict[str, str]] = []


class AwsConfigRequest(BaseModel):
    mode: str = "live"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_profile: str | None = None
    region: str = "us-east-1"


class LlmConfigRequest(BaseModel):
    provider: str = "auto"
    api_key: str = ""
    model: str = ""


@app.get("/", response_class=HTMLResponse)
def web_dashboard() -> HTMLResponse:
    """Serve the built-in CloudBill Detective Web Frontend."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>cloudbill-detective MCP server is running.</h1>")
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/health")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "cloudbill-detective", "policy": "strictly-read-only"}


@app.get("/tools/aws_status")
def http_get_aws_status() -> dict[str, Any]:
    """Return current mode (live vs fixture), AWS STS caller identity, and LLM status."""
    st = get_aws_connection_status()
    st["llm"] = get_llm_status()
    return st


@app.post("/tools/llm_config")
def http_post_llm_config(req: LlmConfigRequest) -> dict[str, str]:
    """Configure the LLM provider (bedrock, gemini, groq, openai, ollama, or none)."""
    return set_llm_config(provider=req.provider, api_key=req.api_key, model=req.model)


@app.post("/tools/aws_disconnect")
def http_post_aws_disconnect() -> dict[str, Any]:
    """Clear in-memory AWS credentials and switch to fixture mode."""
    return clear_runtime_aws_config()


@app.get("/tools/allowed_regions")
def http_get_allowed_regions() -> dict[str, Any]:
    """Probe common AWS regions using read-only ec2:DescribeVolumes to find which regions
    are permitted by the AWS Organization's Service Control Policy (SCP).
    """
    from concurrent.futures import ThreadPoolExecutor
    from cloudbill_detective.aws_client import get_readonly_boto3_client, is_fixture_mode

    candidate_regions = [
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "ap-south-1",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-northeast-1",
        "eu-west-1",
        "eu-central-1",
    ]
    if is_fixture_mode():
        return {"allowed_regions": ["us-east-1"], "denied_regions": []}

    allowed: list[str] = []
    denied: list[str] = []

    def _check_region(reg: str) -> tuple[str, bool]:
        try:
            ec2 = get_readonly_boto3_client("ec2", region_name=reg)
            ec2.describe_volumes(MaxResults=5)
            return (reg, True)
        except Exception:
            return (reg, False)

    with ThreadPoolExecutor(max_workers=10) as pool:
        for reg, ok in pool.map(_check_region, candidate_regions):
            if ok:
                allowed.append(reg)
            else:
                denied.append(reg)

    return {"allowed_regions": allowed, "denied_regions": denied}


@app.post("/tools/aws_config")
def http_post_aws_config(req: AwsConfigRequest) -> dict[str, Any]:
    """Switch between LIVE AWS and FIXTURE mode and optionally set in-memory AWS credentials."""
    return set_runtime_aws_config(
        mode=req.mode,
        aws_access_key_id=req.aws_access_key_id,
        aws_secret_access_key=req.aws_secret_access_key,
        aws_session_token=req.aws_session_token,
        aws_profile=req.aws_profile,
        region=req.region,
    )


@app.get("/tools/find_waste")
def http_get_find_waste(region: str = Query(default="us-east-1")) -> dict[str, Any]:
    return run_find_waste(region=region)


@app.post("/tools/find_waste")
def http_post_find_waste(req: FindWasteRequest) -> dict[str, Any]:
    return run_find_waste(region=req.region)


@app.get("/tools/who_created")
def http_get_who_created(
    resource_id: str = Query(...),
    region: str = Query(default="us-east-1"),
) -> dict[str, Any]:
    return run_who_created(resource_id=resource_id, region=region)


@app.post("/tools/who_created")
def http_post_who_created(req: WhoCreatedRequest) -> dict[str, Any]:
    return run_who_created(resource_id=req.resource_id, region=req.region)


@app.get("/tools/explain_change")
def http_get_explain_change(
    resource_id: str = Query(...),
    region: str = Query(default="us-east-1"),
) -> dict[str, Any]:
    return run_explain_change(resource_id=resource_id, region=region)


@app.post("/tools/explain_change")
def http_post_explain_change(req: ExplainChangeRequest) -> dict[str, Any]:
    return run_explain_change(resource_id=req.resource_id, region=req.region)


@app.get("/tools/cost_summary")
def http_get_cost_summary(
    days: int = Query(default=30, ge=1),
    group_by: str = Query(default="SERVICE"),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return run_cost_summary(days=days, group_by=group_by, force_refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/tools/cost_summary")
def http_post_cost_summary(req: CostSummaryRequest) -> dict[str, Any]:
    try:
        return run_cost_summary(days=req.days, group_by=req.group_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tools/eval_score")
def http_get_eval_score(
    region: str = Query(default="us-east-1"),
    mode: str | None = Query(default=None),
) -> dict[str, Any]:
    """Run the eval scorer against either 'fixture' ground_truth.json or 'live' AWS region."""
    from cloudbill_detective.aws_client import is_fixture_mode
    from evals.scorer import score_results

    if mode is not None:
        live_mode = mode.strip().lower() == "live"
    else:
        live_mode = not is_fixture_mode()
    return score_results(live=live_mode, region=region)


@app.post("/tools/ask")
def http_ask_detective(req: AskDetectiveRequest) -> dict[str, Any]:
    """Natural-language router that uses a configured LLM (with MCP tool calling) if enabled,
    or falls back to the built-in deterministic MCP tool orchestrator.
    """
    q = req.query.strip()
    q_lower = q.lower()

    # 0. Try live LLM Agent with MCP tool-calling if an LLM provider is configured
    llm_res = try_llm_agent(query=q, region=req.region, history=req.history)
    llm_error_note: str | None = None
    if llm_res and llm_res.get("ok"):
        return {
            "answer": llm_res["answer"],
            "focused_resource_id": None,
            "tool_called": ",".join(llm_res.get("tools_used", [])) or "llm_agent",
            "report": {
                "type": "llm_response",
                "title": f"🤖 {llm_res['provider']}",
                "headline": llm_res["answer"],
                "tools_used": llm_res.get("tools_used", []),
            },
        }
    elif llm_res and not llm_res.get("ok"):
        llm_error_note = f"LLM ({llm_res.get('provider')}): {llm_res.get('error')}"

    # 1. Specific resource investigation (vol-*, i-*, snap-*, eipalloc-*)
    resource_match = re.search(r"\b(vol-[a-z0-9]+|i-[a-z0-9]+|snap-[a-z0-9]+|eipalloc-[a-z0-9]+)\b", q_lower)
    if resource_match:
        rid = resource_match.group(1)
        if "who" in q_lower and "explain" not in q_lower:
            who = run_who_created(resource_id=rid, region=req.region)
            if who.get("found"):
                headline = f"Resource {rid} was created by '{who['user']}' via {who['event_name']}."
                ans = (
                    f"{headline}\n"
                    f"• Timestamp: {who['event_time']}\n"
                    f"• Source IP: {who.get('source_ip') or 'N/A'}\n"
                    f"• User Agent: {who.get('user_agent') or 'N/A'}"
                )
            else:
                headline = f"Could not find creation event for {rid} in {req.region}."
                ans = who.get("message", headline)
            return {
                "answer": ans,
                "focused_resource_id": rid,
                "tool_called": "who_created",
                "report": {
                    "type": "resource_attribution",
                    "title": f"CloudTrail Attribution: {rid}",
                    "headline": headline,
                    "tools_used": [f"who_created('{rid}', '{req.region}')"],
                    "attribution": who,
                },
            }

        exp = run_explain_change(resource_id=rid, region=req.region)
        return {
            "answer": exp["explanation"],
            "focused_resource_id": rid,
            "tool_called": "explain_change",
            "report": {
                "type": "resource_explanation",
                "title": f"Cost & Change Explanation: {rid}",
                "headline": exp["explanation"],
                "tools_used": [
                    f"find_waste('{req.region}')",
                    f"who_created('{rid}', '{req.region}')",
                    f"explain_change('{rid}', '{req.region}')",
                ],
                "explanation": exp,
            },
        }

    # 2. Full Bill Growth / Cost Summary / Waste Investigation
    by_service = run_cost_summary(days=30, group_by="SERVICE")
    by_region = run_cost_summary(days=30, group_by="REGION")
    by_usage = run_cost_summary(days=30, group_by="USAGE_TYPE")
    waste = run_find_waste(region=req.region)

    total_spend = by_service.get("total_cost_usd", 0.0)
    credits_applied = by_service.get("credits_applied_usd", 0.0)
    net_bill = by_service.get("net_after_credits_usd", total_spend)
    waste_cost = waste.get("total_estimated_monthly_cost", 0.0)
    waste_count = waste.get("waste_count", 0)

    service_items = []
    for item in by_service.get("breakdown", [])[:6]:
        pct = round((item["cost_usd"] / total_spend) * 100, 1) if total_spend > 0 else 0.0
        service_items.append(
            {
                "name": item["group"],
                "cost_usd": item["cost_usd"],
                "share_pct": pct,
            }
        )

    region_items = []
    for item in by_region.get("breakdown", [])[:5]:
        pct = round((item["cost_usd"] / total_spend) * 100, 1) if total_spend > 0 else 0.0
        region_items.append(
            {
                "name": item["group"],
                "cost_usd": item["cost_usd"],
                "share_pct": pct,
            }
        )

    usage_items = []
    for item in by_usage.get("breakdown", [])[:6]:
        pct = round((item["cost_usd"] / total_spend) * 100, 1) if total_spend > 0 else 0.0
        usage_items.append(
            {
                "name": item["group"],
                "cost_usd": item["cost_usd"],
                "share_pct": pct,
            }
        )

    top_waste = sorted(waste.get("resources", []), key=lambda r: r["estimated_monthly_cost"], reverse=True)
    enriched_waste = []
    for item in top_waste:
        who = run_who_created(item["resource_id"], region=req.region)
        enriched_waste.append(
            {
                **item,
                "created_by": who.get("user", "unknown"),
                "creating_event": who.get("event_name", "N/A"),
                "created_at": who.get("event_time", "N/A"),
            }
        )

    # Build human-friendly narrative headline & recommendations
    top_svc_text = (
        f"Your top cost driver is {service_items[0]['name']} (${service_items[0]['cost_usd']:.2f}, "
        f"{service_items[0]['share_pct']}% of total spend)"
        if service_items
        else "No billable service usage was recorded in the last 30 days"
    )
    top_reg_text = (
        f"concentrated primarily in {region_items[0]['name']} (${region_items[0]['cost_usd']:.2f})"
        if region_items
        else f"in {req.region}"
    )

    if waste_count > 0:
        waste_headline = (
            f"We detected {waste_count} wasted resources in {req.region} adding "
            f"${waste_cost:.2f}/month in avoidable charges."
        )
    elif waste.get("warnings"):
        waste_headline = (
            f"EC2/EBS waste scan in {req.region} encountered an AWS Organization SCP restriction. "
            f"Check your active spend region ({region_items[0]['name'] if region_items else req.region})."
        )
    else:
        waste_headline = (
            f"Good news: $0.00/month of idle EC2/EBS/Elastic IP waste was found in {req.region} "
            f"— your ${total_spend:.2f} usage comes from active services rather than orphaned resources."
        )

    credit_sentence = (
        f" (${credits_applied:.2f} covered by AWS Trial/Promotional Credits → ${net_bill:.2f} net bill)"
        if credits_applied > 0
        else ""
    )

    headline = (
        f"Over the last 30 days, your AWS account generated ${total_spend:.2f} in gross usage{credit_sentence}. "
        f"{top_svc_text}, {top_reg_text}. {waste_headline}"
    )

    recommendations: list[str] = []
    if enriched_waste:
        top_w = enriched_waste[0]
        recommendations.append(
            f"Clean up {top_w['resource_id']} ({top_w['reason']}, created by {top_w['created_by']}) "
            f"to immediately save ${top_w['estimated_monthly_cost']:.2f}/month."
        )
    if service_items:
        recommendations.append(
            f"Review {service_items[0]['name']} (${service_items[0]['cost_usd']:.2f} / {service_items[0]['share_pct']}% of spend), "
            f"which is your #1 service driver over the past 30 days."
        )
    if usage_items:
        recommendations.append(
            f"Top usage type is '{usage_items[0]['name']}' at ${usage_items[0]['cost_usd']:.2f} ({usage_items[0]['share_pct']}%)."
        )
    if region_items and region_items[0]["name"] not in (req.region, "NoRegion"):
        recommendations.append(
            f"Most of your spend is in region '{region_items[0]['name']}' (${region_items[0]['cost_usd']:.2f}) — "
            f"switch the region selector to '{region_items[0]['name']}' to scan that region for waste."
        )

    # Readable plain-text fallback
    text_lines = [headline, "", "Top Services (30 Days):"]
    for s in service_items:
        text_lines.append(f"  • {s['name']}: ${s['cost_usd']:.2f} ({s['share_pct']}%)")
    if enriched_waste:
        text_lines.append(f"\nDetected Waste in {req.region} (${waste_cost:.2f}/month):")
        for w in enriched_waste:
            text_lines.append(
                f"  • {w['resource_id']} (${w['estimated_monthly_cost']:.2f}/mo): {w['reason']} — created by '{w['created_by']}'"
            )

    return {
        "answer": "\n".join(text_lines),
        "focused_resource_id": enriched_waste[0]["resource_id"] if enriched_waste else None,
        "tool_called": "cost_summary+find_waste+explain_change",
        "report": {
            "type": "bill_investigation",
            "title": f"30-Day AWS Bill Growth Investigation ({req.region})",
            "headline": headline,
            "total_spend_usd": total_spend,
            "credits_applied_usd": credits_applied,
            "net_bill_usd": net_bill,
            "waste_monthly_usd": waste_cost,
            "waste_count": waste_count,
            "region_scanned": req.region,
            "top_services": service_items,
            "top_regions": region_items,
            "top_usage_types": usage_items,
            "waste_items": enriched_waste,
            "warnings": waste.get("warnings", []),
            "llm_note": llm_error_note,
            "recommendations": recommendations,
            "tools_used": [
                "cost_summary(30, 'SERVICE')",
                "cost_summary(30, 'REGION')",
                "cost_summary(30, 'USAGE_TYPE')",
                f"find_waste('{req.region}')",
            ],
        },
    }


# Mount FastMCP SSE app under /mcp so MCP HTTP clients can connect to /mcp/sse
try:
    app.mount("/mcp", mcp.sse_app())
except Exception:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="cloudbill-detective MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' (default for MCP clients) or 'http' (Web Dashboard + FastAPI + MCP SSE)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port (default: 8000)")
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        print(f"\n[cloudbill-detective] Web Dashboard & MCP HTTP Server running at: http://{args.host}:{args.port}")
        print(f"[cloudbill-detective] MCP SSE Endpoint: http://{args.host}:{args.port}/mcp/sse\n")
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
