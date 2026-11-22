"""AWS monthly cost estimation helpers (on-demand us-east-1 baseline pricing)."""

from __future__ import annotations

HOURS_PER_MONTH = 730.0

# EBS volume pricing per GB-month (USD)
EBS_VOLUME_PRICING_PER_GB_MONTH: dict[str, float] = {
    "gp3": 0.08,
    "gp2": 0.10,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,
}

# EBS snapshot storage pricing per GB-month (USD)
EBS_SNAPSHOT_PRICE_PER_GB_MONTH: float = 0.05

# Unassociated Public IPv4 / Elastic IP charge: $0.005/hour * 730 hours = $3.65/month
UNASSOCIATED_EIP_MONTHLY_COST: float = round(0.005 * HOURS_PER_MONTH, 2)

# Common EC2 Linux On-Demand hourly prices (USD/hr, us-east-1 baseline)
EC2_HOURLY_PRICING: dict[str, float] = {
    "t2.nano": 0.0058,
    "t2.micro": 0.0116,
    "t2.small": 0.023,
    "t2.medium": 0.0464,
    "t3.nano": 0.0052,
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    "t3a.micro": 0.0094,
    "t3a.small": 0.0188,
    "t3a.medium": 0.0376,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m5.2xlarge": 0.384,
    "m6i.large": 0.096,
    "m6i.xlarge": 0.192,
    "c5.large": 0.085,
    "c5.xlarge": 0.17,
    "c6i.large": 0.085,
    "r5.large": 0.126,
    "r5.xlarge": 0.252,
}

DEFAULT_EC2_HOURLY_PRICE: float = 0.05


def estimate_ebs_volume_monthly_cost(
    size_gb: int | float,
    volume_type: str = "gp3",
    iops: int | None = None,
) -> float:
    """Estimate monthly cost (USD) for an EBS volume."""
    rate = EBS_VOLUME_PRICING_PER_GB_MONTH.get(volume_type.lower(), 0.08)
    base_cost = float(size_gb) * rate
    extra_iops_cost = 0.0
    if volume_type.lower() in ("io1", "io2") and iops:
        extra_iops_cost = float(iops) * 0.065
    elif volume_type.lower() == "gp3" and iops and iops > 3000:
        extra_iops_cost = float(iops - 3000) * 0.005
    return round(base_cost + extra_iops_cost, 2)


def estimate_eip_monthly_cost() -> float:
    """Estimate monthly cost (USD) for an unassociated Elastic IP address."""
    return UNASSOCIATED_EIP_MONTHLY_COST


def estimate_ec2_monthly_cost(instance_type: str) -> float:
    """Estimate monthly on-demand cost (USD) for a running EC2 instance."""
    hourly = EC2_HOURLY_PRICING.get(instance_type.lower(), DEFAULT_EC2_HOURLY_PRICE)
    return round(hourly * HOURS_PER_MONTH, 2)


def estimate_snapshot_monthly_cost(volume_size_gb: int | float) -> float:
    """Estimate monthly storage cost (USD) for an EBS snapshot."""
    return round(float(volume_size_gb) * EBS_SNAPSHOT_PRICE_PER_GB_MONTH, 2)

