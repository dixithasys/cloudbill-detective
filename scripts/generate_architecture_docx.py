"""Generate a clear, beginner-to-advanced Word DOCX guide:
CloudBill_Detective_Architecture_and_Code_Guide.docx
Explains:
1. What is MCP & What is a Client vs Server?
2. The 3 Major Components of CloudBill Detective
3. Deep Dive into the 4 MCP Tools & Read-Only Security Guard
4. Step-by-Step Real Example ("Why did my AWS bill grow?")
5. Complete File-by-File Code Guide
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

OUTPUT_DOCX = Path(__file__).resolve().parent.parent / "CloudBill_Detective_Architecture_and_Code_Guide.docx"

CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

DOC_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>
        <w:sz w:val="22"/>
        <w:color w:val="1E293B"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr><w:spacing w:after="140" w:line="276" w:lineRule="auto"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:pPr><w:spacing w:before="120" w:after="220"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="46"/><w:color w:val="0F172A"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:pPr>
      <w:spacing w:before="320" w:after="140"/>
      <w:pBdr><w:bottom w:val="single" w:sz="8" w:space="4" w:color="2563EB"/></w:pBdr>
    </w:pPr>
    <w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="1D4ED8"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:pPr><w:spacing w:before="240" w:after="100"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="26"/><w:color w:val="0F172A"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="CodeBlock">
    <w:name w:val="CodeBlock"/>
    <w:pPr>
      <w:spacing w:before="50" w:after="50" w:line="240" w:lineRule="auto"/>
      <w:shd w:val="clear" w:color="auto" w:fill="F1F5F9"/>
      <w:ind w:left="200" w:right="200"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>
      <w:sz w:val="19"/>
      <w:color w:val="0F172A"/>
    </w:rPr>
  </w:style>
</w:styles>
"""


def p(text: str, style: str = "Normal", bold_prefix: str = "") -> str:
    runs = []
    if bold_prefix:
        runs.append(f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{escape(bold_prefix)}</w:t></w:r>')
    if text:
        runs.append(f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>')
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{"".join(runs)}</w:p>'


def bullet(bold_label: str, text: str) -> str:
    return (
        '<w:p>'
        '<w:pPr><w:ind w:left="420" w:hanging="240"/><w:spacing w:after="100"/></w:pPr>'
        f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">• {escape(bold_label)}</w:t></w:r>'
        f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
        '</w:p>'
    )


def code_block(lines: list[str]) -> str:
    out = []
    for line in lines:
        out.append(
            '<w:p>'
            '<w:pPr><w:pStyle w:val="CodeBlock"/></w:pPr>'
            f'<w:r><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(line)}</w:t></w:r>'
            '</w:p>'
        )
    return "".join(out)


def table(headers: list[str], rows: list[list[str]]) -> str:
    borders = (
        '<w:tblBorders>'
        '<w:top w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
        '<w:left w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
        '<w:bottom w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
        '<w:right w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
        '<w:insideH w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
        '<w:insideV w:val="single" w:sz="6" w:space="0" w:color="CBD5E1"/>'
        '</w:tblBorders>'
    )
    tbl_pr = f'<w:tblPr><w:tblW w:w="5000" w:type="pct"/>{borders}</w:tblPr>'

    tr_head_cells = []
    for h in headers:
        tr_head_cells.append(
            '<w:tc>'
            '<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="EFF6FF"/><w:tcMar>'
            '<w:top w:w="100" w:type="dxa"/><w:bottom w:w="100" w:type="dxa"/>'
            '<w:left w:w="140" w:type="dxa"/><w:right w:w="140" w:type="dxa"/></w:tcMar></w:tcPr>'
            f'<w:p><w:r><w:rPr><w:b/><w:color w:val="1E3A8A"/></w:rPr><w:t>{escape(h)}</w:t></w:r></w:p>'
            '</w:tc>'
        )
    trs = [f'<w:tr>{"".join(tr_head_cells)}</w:tr>']

    for row in rows:
        tcs = []
        for cell in row:
            tcs.append(
                '<w:tc>'
                '<w:tcPr><w:tcMar>'
                '<w:top w:w="90" w:type="dxa"/><w:bottom w:w="90" w:type="dxa"/>'
                '<w:left w:w="140" w:type="dxa"/><w:right w:w="140" w:type="dxa"/></w:tcMar></w:tcPr>'
                f'<w:p><w:r><w:t xml:space="preserve">{escape(cell)}</w:t></w:r></w:p>'
                '</w:tc>'
            )
        trs.append(f'<w:tr>{"".join(tcs)}</w:tr>')

    return f'<w:tbl>{tbl_pr}{"".join(trs)}</w:tbl><w:p/>'


def build_document_xml() -> str:
    parts: list[str] = []

    parts.append(p("CloudBill Detective — Complete Architecture & Code Explanation", "Title"))
    parts.append(
        p(
            "This guide clearly explains what MCP (Model Context Protocol) is, what a Client vs. Server is, "
            "the 3 Major Components of CloudBill Detective, and how every file in the codebase works."
        )
    )

    # PART 1
    parts.append(p("1. What is MCP? And What is a 'Client' vs. 'Server'?", "Heading1"))
    parts.append(
        p(
            "Normally, an AI model (LLM) only knows how to generate text — it cannot log into your AWS account, "
            "check Cost Explorer, or look up CloudTrail events on its own. "
            "MCP (Model Context Protocol) is the standard bridge that gives an AI model safe 'hands' (tools) to "
            "fetch real data."
        )
    )
    parts.append(p("Real-World Analogy:", "Heading2"))
    parts.append(
        bullet(
            "The User (You): ",
            "You sit at a restaurant table and ask a question: 'Why did my AWS bill grow this month?'",
        )
    )
    parts.append(
        bullet(
            "Component 1 — The Client / UI (The Menu & Table): ",
            "The screen you interact with (our Web Dashboard at http://127.0.0.1:8000, or an app like Claude Desktop). "
            "It takes your question and displays the final formatted table and charts.",
        )
    )
    parts.append(
        bullet(
            "Component 2 — The AI Orchestrator / MCP Client (The Waiter + Brain): ",
            "It hears your question, looks at the list of available MCP Tools, calls those tools to get real AWS numbers, "
            "and uses the Cost-Free LLM to turn the raw JSON into a clean explanation.",
        )
    )
    parts.append(
        bullet(
            "Component 3 — The MCP Server (The Kitchen / Detective Tools): ",
            "It holds the 4 specialized AWS tools (find_waste, who_created, explain_change, cost_summary) and safely "
            "queries AWS in strictly read-only mode.",
        )
    )

    # PART 2: THE 3 MAJOR COMPONENTS
    parts.append(p("2. The 3 Major Components of CloudBill Detective", "Heading1"))
    parts.append(
        code_block(
            [
                "===============================================================================",
                "   COMPONENT 1: THE CLIENT / FRONTEND (What You See & Click)",
                "   Files: cloudbill_detective/static/index.html (or Claude Desktop / Cursor)",
                "   - Light-theme web dashboard at http://127.0.0.1:8000",
                "   - Multi-turn Chat Thread, 3 KPI Cards, Cost Explorer bars, Waste table",
                "===============================================================================",
                "                                      |",
                "                                      | HTTP (/tools/*) or MCP (stdio / SSE)",
                "                                      v",
                "===============================================================================",
                "   COMPONENT 2: THE AI ORCHESTRATOR & COST-FREE LLM (The Brain)",
                "   Files: cloudbill_detective/llm_agent.py + server.py (/tools/ask)",
                "   - Decides which MCP tools to run for your question",
                "   - Calls the 4 MCP tools and collects their real JSON outputs",
                "   - Sends the grounded AWS facts to the Cost-Free LLM ($0 AWS cost)",
                "===============================================================================",
                "                                      |",
                "                                      | Calls @mcp.tool() Python functions",
                "                                      v",
                "===============================================================================",
                "   COMPONENT 3: THE MCP SERVER & READ-ONLY AWS LAYER (The Hands)",
                "   Files: server.py + tools.py + aws_client.py + pricing.py",
                "   - Exposes the 4 official @mcp.tool() functions:",
                "       1. find_waste(region)",
                "       2. who_created(resource_id, region)",
                "       3. explain_change(resource_id, region)",
                "       4. cost_summary(days, group_by)",
                "   - Enforces STRICTLY READ-ONLY access via ReadOnlyClientProxy",
                "   - Caches Cost Explorer locally for 24h to save $0.01/call AWS fees",
                "===============================================================================",
            ]
        )
    )

    parts.append(p("Component 1: The Client / Frontend UI", "Heading2"))
    parts.append(
        p(
            "In MCP terminology, a 'Client' is any application that connects to our server to use its tools. "
            "Because cloudbill-detective supports both HTTP and stdio, you can use two kinds of clients:"
        )
    )
    parts.append(
        bullet(
            "A. Our Built-in Web Client (cloudbill_detective/static/index.html): ",
            "Runs in your browser at http://127.0.0.1:8000. It provides the 3 KPI summary cards (30-Day Gross Usage, "
            "Credits Applied, Detected Monthly Waste), the multi-turn Chat box, the Cost Explorer Breakdown chart "
            "(grouped by Service, Region, or Usage Type), the Idle Resource table, and the CloudTrail Attribution lookup.",
        )
    )
    parts.append(
        bullet(
            "B. External MCP Clients (Claude Desktop, Cursor, MCP Inspector): ",
            "Can connect directly to cloudbill-detective over '--transport stdio' or HTTP '/mcp/sse' using the "
            "official MCP protocol.",
        )
    )

    parts.append(p("Component 2: The AI Orchestrator & Cost-Free LLM Engine", "Heading2"))
    parts.append(
        p(
            "Implemented in cloudbill_detective/llm_agent.py. When you ask a question in the chat box, "
            "this component coordinates between the MCP tools and the LLM:"
        )
    )
    parts.append(
        bullet(
            "Step 1 (Tool Execution): ",
            "It calls _execute_mcp_tool() to run cost_summary(30, 'SERVICE'), cost_summary(30, 'REGION'), "
            "cost_summary(30, 'USAGE_TYPE'), find_waste(region), and explain_change() on your #1 cost-driving service.",
        )
    )
    parts.append(
        bullet(
            "Step 2 (Cost-Free LLM Synthesis): ",
            "It sends the real JSON returned by those tools (plus your chat history) to a Cost-Free LLM "
            "(by default the keyless Free Cloud LLM, or optionally Google Gemini Free Tier, Groq Free Tier, or local Ollama). "
            "It never calls AWS Bedrock, so it costs $0.00 on your AWS bill.",
        )
    )

    parts.append(p("Component 3: The MCP Tool Server & Read-Only AWS Security Layer", "Heading2"))
    parts.append(
        p(
            "Implemented across server.py, tools.py, aws_client.py, and pricing.py. This component is the actual "
            "MCP Server that talks to AWS (or offline fixture files when in Demo Mode)."
        )
    )
    parts.append(
        bullet(
            "Official MCP Registration (server.py): ",
            "Creates mcp = MCPServer('cloudbill-detective') and registers the 4 tools with @mcp.tool().",
        )
    )
    parts.append(
        bullet(
            "Strictly Read-Only Security Guard (aws_client.py): ",
            "Wraps every boto3 client in ReadOnlyClientProxy. It only allows AWS API methods starting with "
            "describe_, list_, get_, or lookup_events. Any attempt to call create, modify, delete, or terminate "
            "is blocked immediately in Python with ReadOnlyViolationError.",
        )
    )

    # PART 3: THE 4 MCP TOOLS IN DETAIL
    parts.append(p("3. How Each of the 4 MCP Tools Works (cloudbill_detective/tools.py)", "Heading1"))
    parts.append(
        table(
            ["MCP Tool Name", "AWS Read-Only APIs Called", "What It Computes & Returns"],
            [
                [
                    "1. find_waste(region)",
                    "ec2:DescribeVolumes\nec2:DescribeAddresses\nec2:DescribeInstances\ncloudwatch:GetMetricStatistics\nec2:DescribeSnapshots",
                    "Finds 4 types of waste:\n"
                    "• Unattached EBS volumes (status='available')\n"
                    "• Unassociated Elastic IPs ($3.65/mo)\n"
                    "• Running EC2 instances with 7-day avg CPU < 5%\n"
                    "• EBS snapshots older than 90 days ($0.05/GB-mo)\n"
                    "Returns resource_id, reason, and estimated_monthly_cost.",
                ],
                [
                    "2. who_created(resource_id, region)",
                    "cloudtrail:LookupEvents\nopensearch:ListDomainNames\nopensearchserverless:ListCollections",
                    "Finds who created a resource or service in CloudTrail:\n"
                    "• Searches by ResourceName (for vol-..., i-..., etc.)\n"
                    "• Or maps a service name (like 'Amazon OpenSearch Service') to its CloudTrail EventSource "
                    "(es.amazonaws.com / aoss.amazonaws.com) to find the exact resource, IAM user, event name, and timestamp.",
                ],
                [
                    "3. explain_change(resource_id, region)",
                    "Combines find_waste + cost_summary + who_created",
                    "Produces a plain-English explanation of how much a resource or service adds to your monthly bill "
                    "and who created it according to CloudTrail.",
                ],
                [
                    "4. cost_summary(days, group_by)",
                    "ce:GetCostAndUsage\n(with 24h local cache in .cache/cost_explorer_cache.json)",
                    "Summarizes AWS spend grouped by SERVICE, REGION, or USAGE_TYPE.\n"
                    "• Caches results locally to save the $0.01/call Cost Explorer fee.\n"
                    "• Filters out RECORD_TYPE=Credit/Refund so trial usage ($23.49) is visible alongside Credits Applied.",
                ],
            ],
        )
    )

    # PART 4: REAL STEP-BY-STEP WALKTHROUGH
    parts.append(p("4. Real Example Walkthrough: How It Found Your $18.79 OpenSearch Resource", "Heading1"))
    parts.append(
        p(
            "Here is what happened behind the scenes when you connected your live AWS account (308525896594) "
            "and clicked 'Why did my AWS bill grow?':"
        )
    )
    parts.append(
        bullet(
            "1. Client UI Sends Request: ",
            "The browser sent POST /tools/ask with query='Why did my AWS bill grow?' and region='ap-southeast-2'.",
        )
    )
    parts.append(
        bullet(
            "2. Orchestrator Runs cost_summary(30, 'SERVICE'): ",
            "tools.py queried AWS Cost Explorer (excluding negative Credit lines) and found $23.49 in gross 30-day usage, "
            "with Amazon OpenSearch Service as the #1 driver at $18.79.",
        )
    )
    parts.append(
        bullet(
            "3. Orchestrator Runs cost_summary(30, 'REGION'): ",
            "tools.py found that $23.24 of the $23.49 total spend occurred in ap-southeast-2 (Sydney).",
        )
    )
    parts.append(
        bullet(
            "4. Orchestrator Runs explain_change('Amazon OpenSearch Service', 'ap-southeast-2'): ",
            "who_created() saw the service name 'Amazon OpenSearch Service', mapped it to OpenSearch CloudTrail sources "
            "(es.amazonaws.com and aoss.amazonaws.com) in ap-southeast-2, and discovered the OpenSearch Serverless "
            "collection 'bedrock-knowledge-base-c0fw9c' created by user ID '34087458-a091-70d8-974f-7ca2c792a1b1' "
            "via CreateSecurityPolicy on 2026-11-19T00:16:02+00:00.",
        )
    )
    parts.append(
        bullet(
            "5. Cost-Free LLM Formats the Table: ",
            "llm_agent.py sent those exact tool results to the Free Cloud LLM, which summarized them into the "
            "Service / Cost / Region / Resource ID / Created By table rendered in your chat thread.",
        )
    )

    # PART 5: COMPLETE FILE DIRECTORY
    parts.append(p("5. Summary of Every File in the Project", "Heading1"))
    parts.append(
        table(
            ["File", "Component", "Purpose"],
            [
                ["cloudbill_detective/static/index.html", "1. Client / UI", "Light-theme web dashboard, chat thread, charts, and modals."],
                ["cloudbill_detective/llm_agent.py", "2. AI Orchestrator", "Connects the 4 MCP tools to the Cost-Free LLM ($0 AWS cost)."],
                ["cloudbill_detective/server.py", "2 & 3. Server Bridge", "Registers @mcp.tool() functions and hosts FastAPI HTTP endpoints."],
                ["cloudbill_detective/tools.py", "3. MCP Tool Server", "Implements find_waste, who_created, explain_change, and cost_summary."],
                ["cloudbill_detective/aws_client.py", "3. Security Layer", "ReadOnlyClientProxy blocking all write calls + in-memory AWS session."],
                ["cloudbill_detective/pricing.py", "3. Pricing Engine", "Monthly cost formulas for EBS volumes, EIPs, EC2 instances, and snapshots."],
                ["tests/fixtures/*.json", "Testing & Demo", "Offline fixture data for Cost Explorer, EC2, CloudWatch, and CloudTrail."],
                ["evals/ground_truth.json, runner.py, scorer.py", "Eval Harness", "Automated benchmark scoring Recall (100%) and False Positives (0)."],
                ["iam/readonly_policy.json", "AWS IAM Security", "Least-privilege read-only IAM policy JSON for your AWS user/role."],
                ["Dockerfile & pyproject.toml", "Packaging", "Python 3.12 container and dependency configuration."],
            ],
        )
    )

    body = "".join(parts)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body>'
        '</w:document>'
    )


def main() -> None:
    doc_xml = build_document_xml()
    with zipfile.ZipFile(OUTPUT_DOCX, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML.strip())
        zf.writestr("_rels/.rels", RELS_XML.strip())
        zf.writestr("word/_rels/document.xml.rels", DOC_RELS_XML.strip())
        zf.writestr("word/styles.xml", STYLES_XML.strip())
        zf.writestr("word/document.xml", doc_xml)
    print(f"Updated DOCX: {OUTPUT_DOCX} ({OUTPUT_DOCX.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
