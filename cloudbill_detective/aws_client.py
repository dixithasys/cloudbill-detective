"""Strictly read-only AWS client wrapper with live AWS credential configuration & fixture mode support."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ALLOWED_READONLY_PREFIXES = (
    "describe_",
    "list_",
    "get_",
    "lookup_events",
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

# In-memory runtime configuration (can be updated via Web UI or environment variables)
_RUNTIME_AWS_CONFIG: dict[str, str | None] = {
    "aws_access_key_id": None,
    "aws_secret_access_key": None,
    "aws_session_token": None,
    "aws_profile": None,
    "region": None,
}


class ReadOnlyViolationError(PermissionError):
    """Raised when any non-read-only AWS API operation is attempted."""


class ReadOnlyClientProxy:
    """Wraps a boto3 client and blocks any method not starting with Describe*/List*/Get*/LookupEvents."""

    def __init__(self, client: Any, service_name: str) -> None:
        self._client = client
        self._service_name = service_name

    def __getattr__(self, name: str) -> Any:
        if name == "exceptions":
            return getattr(self._client, name)
        if not name.startswith(ALLOWED_READONLY_PREFIXES):
            raise ReadOnlyViolationError(
                f"STRICT READ-ONLY POLICY VIOLATION: Attempted to call '{self._service_name}.{name}'. "
                f"Only Describe*, List*, Get*, and LookupEvents operations are permitted."
            )
        return getattr(self._client, name)


def is_fixture_mode() -> bool:
    """Return True when COST_MODE=fixture is set in the environment."""
    return os.getenv("COST_MODE", "live").strip().lower() == "fixture"


def clear_runtime_aws_config() -> dict[str, Any]:
    """Wipe any in-memory AWS keys from _RUNTIME_AWS_CONFIG and switch to fixture mode."""
    for k in _RUNTIME_AWS_CONFIG:
        _RUNTIME_AWS_CONFIG[k] = None
    os.environ["COST_MODE"] = "fixture"
    return get_aws_connection_status()


def set_runtime_aws_config(
    mode: str = "live",
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    aws_session_token: str | None = None,
    aws_profile: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Update runtime mode ('live' or 'fixture') and optional AWS credentials in memory."""
    os.environ["COST_MODE"] = mode.strip().lower()
    if aws_access_key_id is not None:
        _RUNTIME_AWS_CONFIG["aws_access_key_id"] = aws_access_key_id.strip() or None
    if aws_secret_access_key is not None:
        _RUNTIME_AWS_CONFIG["aws_secret_access_key"] = aws_secret_access_key.strip() or None
    if aws_session_token is not None:
        _RUNTIME_AWS_CONFIG["aws_session_token"] = aws_session_token.strip() or None
    if aws_profile is not None:
        _RUNTIME_AWS_CONFIG["aws_profile"] = aws_profile.strip() or None
    if region is not None and region.strip():
        _RUNTIME_AWS_CONFIG["region"] = region.strip()
        os.environ["AWS_REGION"] = region.strip()

    return get_aws_connection_status()


def _create_boto3_session(region_name: str = "us-east-1") -> boto3.Session:
    """Create a boto3 Session using in-memory credentials if provided, else default AWS credential chain."""
    kwargs: dict[str, Any] = {"region_name": region_name}
    ak = (_RUNTIME_AWS_CONFIG.get("aws_access_key_id") or "").strip()
    sk = (_RUNTIME_AWS_CONFIG.get("aws_secret_access_key") or "").strip()
    st = (_RUNTIME_AWS_CONFIG.get("aws_session_token") or "").strip()
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        # Permanent IAM user keys (AKIA...) never use a session token
        if st and not ak.upper().startswith("AKIA"):
            kwargs["aws_session_token"] = st
    elif _RUNTIME_AWS_CONFIG.get("aws_profile"):
        kwargs["profile_name"] = _RUNTIME_AWS_CONFIG["aws_profile"]
    return boto3.Session(**kwargs)


def get_readonly_boto3_client(service_name: str, region_name: str = "us-east-1") -> ReadOnlyClientProxy:
    """Create a boto3 client wrapped in ReadOnlyClientProxy."""
    session = _create_boto3_session(region_name=region_name)
    raw_client = session.client(service_name, region_name=region_name)
    return ReadOnlyClientProxy(raw_client, service_name)


def get_aws_connection_status() -> dict[str, Any]:
    """Verify current mode and test live AWS identity using read-only sts:GetCallerIdentity."""
    mode = "fixture" if is_fixture_mode() else "live"
    region = _RUNTIME_AWS_CONFIG.get("region") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"

    if mode == "fixture":
        return {
            "mode": "fixture",
            "connected": False,
            "region": region,
            "account_id": "fixture-demo-account",
            "arn": "arn:aws:iam::000000000000:user/fixture-mode",
            "message": "Running in offline FIXTURE mode (reading from tests/fixtures/). Switch to LIVE mode to query your real AWS account.",
        }

    try:
        sts = get_readonly_boto3_client("sts", region_name=region)
        identity = sts.get_caller_identity()
        return {
            "mode": "live",
            "connected": True,
            "region": region,
            "account_id": identity.get("Account"),
            "arn": identity.get("Arn"),
            "user_id": identity.get("UserId"),
            "message": f"Connected to live AWS Account {identity.get('Account')} ({identity.get('Arn')})",
        }
    except (BotoCoreError, ClientError, Exception) as exc:
        return {
            "mode": "live",
            "connected": False,
            "region": region,
            "account_id": None,
            "arn": None,
            "error": str(exc),
            "message": f"Live mode active, but AWS credentials failed or are not configured: {exc}",
        }


def get_fixtures_dir() -> Path:
    """Return the path to the fixtures directory."""
    custom = os.getenv("CLOUDBILL_FIXTURES_DIR")
    if custom:
        return Path(custom)
    return DEFAULT_FIXTURES_DIR


def load_fixture_json(filename: str) -> dict[str, Any]:
    """Load a JSON fixture file from tests/fixtures."""
    path = get_fixtures_dir() / filename
    if not path.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_iso_datetime(value: str | datetime) -> datetime:
    """Parse an ISO-8601 string or datetime into a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    cleaned = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
