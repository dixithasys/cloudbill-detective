# cloudbill-detective

**`cloudbill-detective`** is a strictly read-only Python 3.12 MCP (Model Context Protocol) and FastAPI server that explains **why an AWS bill grew** by detecting idle/unattached resources, attributing who created them via AWS CloudTrail, and summarizing spend via AWS Cost Explorer with a local cache to save API costs.

---

## Key Features & Safety Guarantees

- **Strictly Read-Only Architecture**:
  - Every `boto3` client is wrapped in `ReadOnlyClientProxy` ([cloudbill_detective/aws_client.py](cloudbill_detective/aws_client.py)), which programmatically rejects any method call that does not begin with `describe_`, `list_`, `get_`, or `lookup_events`.
  - Ships with a least-privilege IAM Read-Only Policy ([iam/readonly_policy.json](iam/readonly_policy.json)).
- **Dual Transport Support**:
  - **`stdio`**: Official MCP Python SDK (`FastMCP`) over standard I/O for Claude Desktop and local MCP clients.
  - **`http`**: FastAPI application exposing both MCP over SSE (`/mcp/sse`) and JSON REST endpoints (`/tools/*`, `/health`).
- **Cost Explorer Caching & Fixture Mode**:
  - AWS Cost Explorer charges **\$0.01 per API call**. `cost_summary` caches responses locally in `.cache/cost_explorer_cache.json` (24-hour TTL) and reports `cache_hit` and `api_cost_saved_usd`.
  - Setting `COST_MODE=fixture` reads from `tests/fixtures/` instead of calling AWS so you can develop, test, and run the evaluation suite offline at **\$0.00 cost**.

---

## Tools Provided

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `find_waste` | `region: str = "us-east-1"` | Finds unattached EBS volumes (`available`), unassociated Elastic IPs, running EC2 instances with 7-day average CPU < 5%, and EBS snapshots older than 90 days. Returns `resource_id`, `reason`, and `estimated_monthly_cost`. |
| `who_created` | `resource_id: str`, `region: str = "us-east-1"` | Queries CloudTrail `LookupEvents` (`ResourceName=resource_id`) to identify the creating event (`CreateVolume`, `AllocateAddress`, `RunInstances`, `CreateSnapshot`), IAM user/role, and timestamp. |
| `explain_change` | `resource_id: str`, `region: str = "us-east-1"` | Combines `find_waste` and `who_created` into a concise plain-English explanation with estimated monthly cost impact. |
| `cost_summary` | `days: int = 30`, `group_by: str = "SERVICE"` | Queries AWS Cost Explorer (`GetCostAndUsage`) grouped by dimension (`SERVICE`, `REGION`, `USAGE_TYPE`, etc.) with local disk caching and `COST_MODE=fixture` support. |

---

## Quick Start

### 1. Installation (Python 3.12 / `uv`)

```bash
# Using uv
uv venv --python 3.12
uv pip install -e ".[dev]"

# Or using standard pip
pip install -e ".[dev]"
```

### 2. Run in `stdio` Mode (for Claude Desktop / MCP Inspector)

```bash
# Offline fixture mode
COST_MODE=fixture python -m cloudbill_detective.server --transport stdio

# Live AWS mode (uses standard AWS credentials / profile)
AWS_PROFILE=readonly-detective python -m cloudbill_detective.server --transport stdio
```

### 3. Run in HTTP Mode (FastAPI + MCP SSE)

```bash
COST_MODE=fixture python -m cloudbill_detective.server --transport http --host 0.0.0.0 --port 8000
```

Test HTTP endpoints:
```bash
curl http://localhost:8000/health
curl "http://localhost:8000/tools/find_waste?region=us-east-1"
curl "http://localhost:8000/tools/who_created?resource_id=i-0f9e8d7c6b5a00001"
curl "http://localhost:8000/tools/explain_change?resource_id=i-0f9e8d7c6b5a00001"
curl "http://localhost:8000/tools/cost_summary?days=30&group_by=SERVICE"
```

---

## Claude Desktop Configuration

Add the following to your `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

### Option A: Local Python / `uv`

```json
{
  "mcpServers": {
    "cloudbill-detective": {
      "command": "python",
      "args": [
        "-m",
        "cloudbill_detective.server",
        "--transport",
        "stdio"
      ],
      "env": {
        "AWS_REGION": "us-east-1",
        "AWS_PROFILE": "default",
        "COST_MODE": "live"
      }
    }
  }
}
```

*(Tip: Change `"COST_MODE": "fixture"` to test in Claude Desktop with the built-in fixture dataset without needing AWS credentials.)*

### Option B: Docker (`stdio`)

Build the image first:
```bash
docker build -t cloudbill-detective:latest .
```

Then configure Claude Desktop:
```json
{
  "mcpServers": {
    "cloudbill-detective": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "AWS_ACCESS_KEY_ID",
        "-e",
        "AWS_SECRET_ACCESS_KEY",
        "-e",
        "AWS_SESSION_TOKEN",
        "-e",
        "AWS_REGION=us-east-1",
        "-e",
        "COST_MODE=live",
        "cloudbill-detective:latest",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

### Option C: Docker (HTTP Server on port 8000)

```bash
docker run --rm -p 8000:8000 -e COST_MODE=fixture cloudbill-detective:latest --transport http --host 0.0.0.0 --port 8000
```

---

## Evaluation Harness (`/evals`)

The `/evals` directory contains:
- [`evals/ground_truth.json`](evals/ground_truth.json): 7 planted waste resources across all 4 categories + 5 healthy distractor resources (to test false positives).
- [`evals/runner.py`](evals/runner.py): Executes `find_waste`, `who_created`, `explain_change`, and `cost_summary` and saves `evals/results.json`.
- [`evals/scorer.py`](evals/scorer.py): Scores detection outputs against ground truth and outputs Recall, Precision, False Positives, Cost Accuracy, Creator Attribution Accuracy, and a Markdown table (`evals/report.md`).

Run the evaluation suite:
```bash
python evals/runner.py
python evals/scorer.py
```

---

## Live AWS Test Scripts (`/scripts`)

> **Warning**: These scripts create real (tiny) AWS resources in your account. Review them carefully before running, and always run `./scripts/teardown.sh` immediately after testing.

- [`scripts/plant_waste.sh`](scripts/plant_waste.sh): Creates a 1 GB unattached `gp3` EBS volume, an unassociated Elastic IP, and an idle `t3.micro` instance tagged with `Project=cloudbill-detective-test`.
- [`scripts/teardown.sh`](scripts/teardown.sh): Terminates the `t3.micro` instance, releases the Elastic IP, and deletes the 1 GB EBS volume.

---

## IAM Read-Only Policy

Attach [`iam/readonly_policy.json`](iam/readonly_policy.json) to the IAM Role or User running `cloudbill-detective`. It grants only:
- `ec2:DescribeVolumes`, `ec2:DescribeAddresses`, `ec2:DescribeInstances`, `ec2:DescribeSnapshots`, `ec2:DescribeRegions`
- `cloudwatch:GetMetricStatistics`, `cloudwatch:GetMetricData`, `cloudwatch:ListMetrics`
- `cloudtrail:LookupEvents`
- `ce:GetCostAndUsage`

