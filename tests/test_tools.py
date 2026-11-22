"""Unit and integration tests for cloudbill-detective."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cloudbill_detective.aws_client import ReadOnlyClientProxy, ReadOnlyViolationError
from cloudbill_detective.server import app
from cloudbill_detective.tools import (
    cost_summary,
    explain_change,
    find_waste,
    who_created,
)
from evals.scorer import score_results


@pytest.fixture(autouse=True)
def fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default tests to COST_MODE=fixture and fixed reference timestamp."""
    monkeypatch.setenv("COST_MODE", "fixture")
    monkeypatch.setenv("CLOUDBILL_REFERENCE_NOW", "2026-11-21T00:00:00Z")


def test_readonly_proxy_blocks_mutations() -> None:
    """Verify ReadOnlyClientProxy strictly blocks any create/modify/delete calls."""
    fake_ec2 = MagicMock()
    proxy = ReadOnlyClientProxy(fake_ec2, "ec2")

    # Allowed read-only operations
    _ = proxy.describe_volumes
    _ = proxy.describe_instances
    _ = proxy.list_tags
    _ = proxy.get_console_output
    _ = proxy.lookup_events

    # Forbidden write/mutation operations must raise ReadOnlyViolationError
    for forbidden in (
        "create_volume",
        "delete_volume",
        "terminate_instances",
        "run_instances",
        "allocate_address",
        "release_address",
        "modify_instance_attribute",
        "stop_instances",
    ):
        with pytest.raises(ReadOnlyViolationError):
            getattr(proxy, forbidden)


def test_find_waste_fixture_mode() -> None:
    """Verify find_waste detects all 7 planted waste items and ignores healthy resources."""
    result = find_waste(region="us-east-1")
    assert result["region"] == "us-east-1"
    assert result["waste_count"] == 7
    assert result["total_estimated_monthly_cost"] == 286.55

    ids = {r["resource_id"] for r in result["resources"]}
    assert ids == {
        "vol-0a1b2c3d4e5f00001",
        "vol-0a1b2c3d4e5f00002",
        "eipalloc-0123456789abcdef1",
        "i-0f9e8d7c6b5a00001",
        "i-0f9e8d7c6b5a00002",
        "snap-0fedcba9876500001",
        "snap-0fedcba9876500002",
    }


def test_who_created_prefers_creation_event() -> None:
    """Verify who_created selects RunInstances over CreateTags when multiple events exist."""
    info = who_created("i-0f9e8d7c6b5a00001")
    assert info["found"] is True
    assert info["event_name"] == "RunInstances"
    assert info["user"] == "charlie.ml"
    assert info["event_time"] == "2026-10-18T19:40:12+00:00"


def test_explain_change_combines_waste_and_creator() -> None:
    """Verify explain_change combines waste cost and creator attribution in plain English."""
    explanation = explain_change("vol-0a1b2c3d4e5f00001")
    assert explanation["is_waste"] is True
    assert explanation["estimated_monthly_cost"] == 40.0
    assert explanation["created_by"] == "alice.dev"
    assert "$40.00/month" in explanation["explanation"]
    assert "alice.dev" in explanation["explanation"]
    assert "CreateVolume" in explanation["explanation"]


def test_cost_summary_fixture_and_caching(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify cost_summary supports COST_MODE=fixture and caches live Cost Explorer calls."""
    # 1. Fixture mode
    res_fixture = cost_summary(days=30, group_by="SERVICE")
    assert res_fixture["source"] == "fixture"
    assert res_fixture["total_cost_usd"] == 612.95
    assert res_fixture["breakdown"][0]["group"] == "Amazon Elastic Compute Cloud - Compute"

    # 2. Live mode with local disk cache avoiding second $0.01 API call
    monkeypatch.setenv("COST_MODE", "live")
    cache_file = tmp_path / "ce_cache.json"
    monkeypatch.setenv("COST_CACHE_PATH", str(cache_file))

    fake_ce = MagicMock()
    fake_ce.get_cost_and_usage.return_value = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-10-22", "End": "2026-11-21"},
                "Groups": [
                    {
                        "Keys": ["Amazon Elastic Block Store"],
                        "Metrics": {"UnblendedCost": {"Amount": "99.50", "Unit": "USD"}},
                    }
                ],
            }
        ]
    }

    with patch("cloudbill_detective.tools.get_readonly_boto3_client", return_value=fake_ce):
        first = cost_summary(days=30, group_by="SERVICE")
        assert first["cache_hit"] is False
        assert first["source"] == "aws_cost_explorer"
        assert first["api_call_cost_incurred_usd"] == 0.01
        assert fake_ce.get_cost_and_usage.call_count == 1

        # Second identical call hits local cache without calling AWS
        second = cost_summary(days=30, group_by="SERVICE")
        assert second["cache_hit"] is True
        assert second["source"] == "cache"
        assert second["api_call_cost_incurred_usd"] == 0.0
        assert second["api_cost_saved_usd"] == 0.01
        assert fake_ce.get_cost_and_usage.call_count == 1


def test_http_endpoints() -> None:
    """Verify FastAPI HTTP endpoints for Web UI, health, and all 4 tools."""
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "cloudbill-detective" in home.text
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/tools/find_waste?region=us-east-1").json()["waste_count"] == 7
    assert client.get("/tools/who_created?resource_id=eipalloc-0123456789abcdef1").json()["user"] == "bob.ops"
    assert client.get("/tools/explain_change?resource_id=snap-0fedcba9876500001").json()["estimated_monthly_cost"] == 20.0
    assert client.get("/tools/cost_summary?days=30&group_by=REGION").json()["total_cost_usd"] == 612.95
    ask_resp = client.post("/tools/ask", json={"query": "Why did my AWS bill grow?"}).json()
    assert "$286.55/month" in ask_resp["answer"]


def test_eval_harness_achieves_full_recall_and_zero_fp(tmp_path: Path) -> None:
    """Verify the eval runner and scorer report 100% recall and 0 false positives."""
    results_file = tmp_path / "results.json"
    report_file = tmp_path / "report.md"
    metrics: dict[str, Any] = score_results(results_path=results_file, report_path=report_file)
    assert metrics["recall"] == 1.0
    assert metrics["false_positives"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["cost_accuracy"] == 1.0
    assert metrics["attribution_accuracy"] == 1.0
    assert report_file.exists()

