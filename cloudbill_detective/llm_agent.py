"""Cost-Free LLM Agent with MCP Tool Calling for cloudbill-detective.
By default, NEVER calls AWS Bedrock so it incurs $0.00 on your AWS account.

Supported Cost-Free LLM Providers:
1. 'free' (Default): Keyless Free Cloud LLM (Pollinations OpenAI-compatible endpoint) + local MCP tool execution ($0 cost, no API key needed).
2. 'gemini': Google Gemini Free Tier (gemini-2.5-flash via Google AI Studio free key).
3. 'groq': Groq Cloud Free Tier (llama-3.3-70b-versatile via console.groq.com free key).
4. 'ollama': 100% Local Ollama (http://localhost:11434).
5. 'openai': OpenAI API (optional).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from cloudbill_detective.tools import (
    cost_summary,
    explain_change,
    find_waste,
    who_created,
)

# Default to 'free' (100% free, zero AWS Bedrock usage)
_RUNTIME_LLM_CONFIG: dict[str, str] = {
    "provider": os.getenv("LLM_PROVIDER", "free"),  # free, gemini, groq, ollama, openai, none
    "api_key": os.getenv("LLM_API_KEY", ""),
    "model": os.getenv("LLM_MODEL", ""),
}

SYSTEM_PROMPT = (
    "You are CloudBill Detective, a strictly read-only AWS FinOps AI assistant. "
    "You have 4 MCP tools: find_waste(region), who_created(resource_id, region), "
    "explain_change(resource_id, region), and cost_summary(days, group_by). "
    "Answer the user's question clearly and concisely using the exact dollar amounts, "
    "service names, regions, resource IDs, and CloudTrail creators from the MCP tool results. "
    "Do not output <thinking> tags."
)

OPENAI_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "find_waste",
            "description": "Find unattached EBS volumes, unassociated Elastic IPs, EC2 instances with 7-day avg CPU < 5%, and snapshots > 90 days old in an AWS region.",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "AWS region, e.g. ap-southeast-2 or us-east-1"}
                },
                "required": ["region"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "who_created",
            "description": "Use CloudTrail LookupEvents to find the creating event, IAM user, and timestamp for a resource_id or AWS service name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string", "description": "AWS resource ID or service name (e.g. 'Amazon OpenSearch Service', 'vol-...', 'i-...')"},
                    "region": {"type": "string", "description": "AWS region"},
                },
                "required": ["resource_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_change",
            "description": "Combine find_waste, cost_summary, and who_created into a plain-English explanation with estimated monthly cost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string", "description": "AWS resource ID or service name"},
                    "region": {"type": "string", "description": "AWS region"},
                },
                "required": ["resource_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cost_summary",
            "description": "Query AWS Cost Explorer with local cache for spend over N days grouped by SERVICE, REGION, or USAGE_TYPE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days (e.g. 30)"},
                    "group_by": {"type": "string", "description": "Dimension: SERVICE, REGION, or USAGE_TYPE"},
                },
                "required": ["days", "group_by"],
            },
        },
    },
]


def _clean_llm_text(text: str) -> str:
    cleaned = re.sub(r"<thinking>[\s\S]*?</thinking>\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<thought>[\s\S]*?</thought>\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def set_llm_config(provider: str, api_key: str = "", model: str = "") -> dict[str, str]:
    _RUNTIME_LLM_CONFIG["provider"] = provider.strip().lower()
    if api_key.strip():
        _RUNTIME_LLM_CONFIG["api_key"] = api_key.strip()
    if model.strip():
        _RUNTIME_LLM_CONFIG["model"] = model.strip()
    return get_llm_status()


def get_llm_status() -> dict[str, str]:
    return {
        "provider": _RUNTIME_LLM_CONFIG["provider"],
        "model": _RUNTIME_LLM_CONFIG["model"] or "default",
        "has_api_key": "yes" if bool(_RUNTIME_LLM_CONFIG["api_key"]) else "no",
    }


def _execute_mcp_tool(name: str, args: dict[str, Any], default_region: str) -> tuple[dict[str, Any], str]:
    """Execute one of the 4 MCP tools and return (result_dict, signature_str)."""
    if name == "find_waste":
        reg = args.get("region") or default_region
        return find_waste(region=reg), f"find_waste('{reg}')"
    if name == "who_created":
        rid = args.get("resource_id", "")
        reg = args.get("region") or default_region
        return who_created(resource_id=rid, region=reg), f"who_created('{rid}', '{reg}')"
    if name == "explain_change":
        rid = args.get("resource_id", "")
        reg = args.get("region") or default_region
        return explain_change(resource_id=rid, region=reg), f"explain_change('{rid}', '{reg}')"
    if name == "cost_summary":
        days = int(args.get("days", 30))
        grp = str(args.get("group_by", "SERVICE"))
        return cost_summary(days=days, group_by=grp), f"cost_summary({days}, '{grp}')"
    return {"error": f"Unknown tool {name}"}, f"{name}()"


def run_free_keyless_llm_agent(
    query: str,
    region: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """100% Free, Keyless Cloud LLM Agent ($0 AWS cost, no API key needed).
    Executes the relevant read-only MCP tools locally first, passes the grounded tool results
    and conversation history to the free Pollinations OpenAI endpoint, and returns the answer.
    """
    tools_called: list[str] = []
    tool_context: dict[str, Any] = {}

    # 1. Gather grounded data from our 4 MCP tools
    q_lower = query.lower()
    res_match = re.search(
        r"\b(vol-[a-z0-9]+|i-[a-z0-9]+|snap-[a-z0-9]+|eipalloc-[a-z0-9]+|bedrock-knowledge-base-[a-z0-9-]+)\b",
        q_lower,
    )

    if res_match:
        rid = res_match.group(1)
        exp, sig = _execute_mcp_tool("explain_change", {"resource_id": rid, "region": region}, region)
        tools_called.append(sig)
        tool_context["explain_change"] = exp
    else:
        svc_sum, sig1 = _execute_mcp_tool("cost_summary", {"days": 30, "group_by": "SERVICE"}, region)
        reg_sum, sig2 = _execute_mcp_tool("cost_summary", {"days": 30, "group_by": "REGION"}, region)
        usg_sum, sig3 = _execute_mcp_tool("cost_summary", {"days": 30, "group_by": "USAGE_TYPE"}, region)
        waste_res, sig4 = _execute_mcp_tool("find_waste", {"region": region}, region)
        tools_called.extend([sig1, sig2, sig3, sig4])

        tool_context["cost_by_service"] = svc_sum.get("breakdown", [])[:6]
        tool_context["total_cost_usd"] = svc_sum.get("total_cost_usd", 0.0)
        tool_context["credits_applied_usd"] = svc_sum.get("credits_applied_usd", 0.0)
        tool_context["net_after_credits_usd"] = svc_sum.get("net_after_credits_usd", 0.0)
        tool_context["cost_by_region"] = reg_sum.get("breakdown", [])[:5]
        tool_context["cost_by_usage_type"] = usg_sum.get("breakdown", [])[:6]
        tool_context["waste_in_region"] = {
            "region": region,
            "waste_count": waste_res.get("waste_count", 0),
            "total_estimated_monthly_cost": waste_res.get("total_estimated_monthly_cost", 0.0),
            "resources": waste_res.get("resources", [])[:5],
        }

        # Automatically run explain_change on the #1 service so we have creator attribution
        top_services = svc_sum.get("breakdown", [])
        if top_services:
            top_service_name = top_services[0]["group"]
            top_reg = (
                reg_sum["breakdown"][0]["group"]
                if reg_sum.get("breakdown") and reg_sum["breakdown"][0]["group"] not in ("NoRegion", "global")
                else region
            )
            exp, sig5 = _execute_mcp_tool(
                "explain_change",
                {"resource_id": top_service_name, "region": top_reg},
                top_reg,
            )
            tools_called.append(sig5)
            tool_context["top_service_attribution"] = exp

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        content = (h.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})

    messages.append(
        {
            "role": "user",
            "content": (
                f"User Question: {query}\n\n"
                f"Live MCP Tool Output (JSON):\n{json.dumps(tool_context, indent=2)}\n\n"
                f"Write a helpful, direct response answering the user's question based strictly on these MCP tool results."
            ),
        }
    )

    payload = {
        "model": "openai",
        "messages": messages,
        "seed": 42,
    }
    req = urllib.request.Request(
        "https://text.pollinations.ai/openai",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "cloudbill-detective/0.1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        resp_data = json.loads(r.read().decode("utf-8"))

    answer_text = _clean_llm_text(resp_data["choices"][0]["message"]["content"])
    return {
        "ok": True,
        "provider": "Free Cloud LLM ($0 Cost • No AWS Bedrock)",
        "answer": answer_text,
        "tools_used": tools_called,
    }


def run_openai_compatible_agent(
    query: str,
    region: str,
    base_url: str,
    api_key: str,
    model: str,
    provider_label: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Run tool-calling loop against Groq (Free Tier), OpenAI, or local Ollama."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        if h.get("content"):
            messages.append({"role": role, "content": h["content"]})
    messages.append({"role": "user", "content": f"[Active AWS Region: {region}] {query}"})
    tools_called: list[str] = []

    for _ in range(5):
        payload = {
            "model": model,
            "messages": messages,
            "tools": OPENAI_TOOLS_SCHEMA,
            "tool_choice": "auto",
        }
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "cloudbill-detective/0.1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            resp_data = json.loads(r.read().decode("utf-8"))

        choice = resp_data["choices"][0]["message"]
        messages.append(choice)

        tool_calls = choice.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"].get("arguments") or "{}")
                t_res, sig = _execute_mcp_tool(fn_name, fn_args, region)
                tools_called.append(sig)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": fn_name,
                        "content": json.dumps(t_res),
                    }
                )
        else:
            return {
                "ok": True,
                "provider": f"{provider_label} ({model} • $0 AWS Cost)",
                "answer": _clean_llm_text(choice.get("content", "")),
                "tools_used": tools_called,
            }

    return {"ok": False, "error": "Max tool iterations reached"}


def run_gemini_agent(
    query: str,
    region: str,
    api_key: str,
    model: str = "gemini-2.5-flash",
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Run tool-calling loop against Google Gemini Free Tier REST API."""
    gemini_decls = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "parameters": t["function"]["parameters"],
        }
        for t in OPENAI_TOOLS_SCHEMA
    ]
    contents: list[dict[str, Any]] = []
    for h in (history or [])[-6:]:
        role = "model" if h.get("role") == "assistant" else "user"
        if h.get("content"):
            contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": f"[Active AWS Region: {region}] {query}"}]})

    tools_called: list[str] = []
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    for _ in range(5):
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "tools": [{"functionDeclarations": gemini_decls}],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            resp_data = json.loads(r.read().decode("utf-8"))

        cand_content = resp_data["candidates"][0]["content"]
        contents.append(cand_content)
        parts = cand_content.get("parts", [])
        fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if fn_calls:
            response_parts = []
            for fc in fn_calls:
                t_res, sig = _execute_mcp_tool(fc["name"], fc.get("args", {}), region)
                tools_called.append(sig)
                response_parts.append(
                    {
                        "functionResponse": {
                            "name": fc["name"],
                            "response": t_res,
                        }
                    }
                )
            contents.append({"role": "user", "parts": response_parts})
        else:
            text_out = "\n".join(p["text"] for p in parts if "text" in p)
            return {
                "ok": True,
                "provider": f"Google Gemini Free Tier ({model} • $0 AWS Cost)",
                "answer": _clean_llm_text(text_out),
                "tools_used": tools_called,
            }

    return {"ok": False, "error": "Max Gemini tool iterations reached"}


def try_llm_agent(
    query: str,
    region: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Run the selected Cost-Free LLM provider (never uses AWS Bedrock)."""
    provider = _RUNTIME_LLM_CONFIG.get("provider", "free")
    api_key = _RUNTIME_LLM_CONFIG.get("api_key", "")
    model = _RUNTIME_LLM_CONFIG.get("model", "")

    if provider == "none":
        return None

    # Auto-detect if user pasted a Gemini or Groq key
    if api_key.startswith("AIza"):
        provider = "gemini"
    elif api_key.startswith("gsk_"):
        provider = "groq"
    elif api_key.startswith("sk-"):
        provider = "openai"
    elif provider in ("auto", "bedrock"):
        provider = "free"

    try:
        if provider == "free":
            return run_free_keyless_llm_agent(query, region, history=history)
        if provider == "gemini":
            if not api_key:
                return {"ok": False, "provider": "gemini", "error": "Missing Gemini API key. Paste a free key from aistudio.google.com/apikey or switch to 'Free Cloud LLM (No Key)'."}
            return run_gemini_agent(query, region, api_key, model or "gemini-2.5-flash", history=history)
        if provider == "groq":
            if not api_key:
                return {"ok": False, "provider": "groq", "error": "Missing Groq API key. Paste a free key from console.groq.com/keys or switch to 'Free Cloud LLM (No Key)'."}
            return run_openai_compatible_agent(
                query,
                region,
                "https://api.groq.com/openai/v1",
                api_key,
                model or "llama-3.3-70b-versatile",
                "Groq Free Tier",
                history=history,
            )
        if provider == "ollama":
            return run_openai_compatible_agent(
                query,
                region,
                "http://localhost:11434/v1",
                "ollama",
                model or "llama3.2",
                "Ollama Local",
                history=history,
            )
        if provider == "openai":
            if not api_key:
                return {"ok": False, "provider": "openai", "error": "Missing OpenAI API key."}
            return run_openai_compatible_agent(
                query,
                region,
                "https://api.openai.com/v1",
                api_key,
                model or "gpt-4o-mini",
                "OpenAI",
                history=history,
            )
    except Exception as exc:
        return {
            "ok": False,
            "provider": provider,
            "error": str(exc),
        }

    return None
