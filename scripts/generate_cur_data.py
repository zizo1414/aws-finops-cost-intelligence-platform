#!/usr/bin/env python3
"""Generates 90 days of realistic multi-account AWS CUR 2.0 billing data with injected cost anomalies and waste."""

from __future__ import annotations

import os
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

WORKLOAD_ACCOUNTS = [
    {
        "account_id": "111122223333",
        "account_name": "prod-core-workloads",
        "cost_center": "CC-2010-CORE-ENG",
        "environment": "Production",
        "owner": "platform-core@enterprise.example",
        "project": "CustomerCheckoutPlatform",
    },
    {
        "account_id": "222233334444",
        "account_name": "enterprise-data-platform",
        "cost_center": "CC-3020-DATA-LAKE",
        "environment": "Production",
        "owner": "data-eng@enterprise.example",
        "project": "EnterpriseLakehouse",
    },
    {
        "account_id": "333344445555",
        "account_name": "ai-ml-genai-sandbox",
        "cost_center": "CC-4050-GENAI-LABS",
        "environment": "Staging",
        "owner": "ai-platform@enterprise.example",
        "project": "CustomerSupportCopilot",
    },
    {
        "account_id": "444455556666",
        "account_name": "shared-network-transit",
        "cost_center": "CC-9000-SHARED-INFRA",
        "environment": "Shared",
        "owner": "cloud-network@enterprise.example",
        "project": "SharedTransitHub",
    },
]

PAYER_ACCOUNT_ID = "999900001111"

SERVICE_PROFILES = [
    {
        "product_code": "AmazonEC2",
        "service_name": "Amazon Elastic Compute Cloud",
        "usage_type": "USW2-BoxUsage:m6i.2xlarge",
        "unit": "Hrs",
        "base_daily_cost": 145.0,
        "pricing_term": "SavingsPlanCoveredUsage",
        "list_multiplier": 1.38,
        "resource_prefix": "i-0a9f8c7d",
    },
    {
        "product_code": "AmazonS3",
        "service_name": "Amazon Simple Storage Service",
        "usage_type": "TimedStorage-ByteHrs",
        "unit": "GB-Mo",
        "base_daily_cost": 48.0,
        "pricing_term": "Usage",
        "list_multiplier": 1.0,
        "resource_prefix": "arn:aws:s3:::enterprise-datalake",
    },
    {
        "product_code": "AWSGlue",
        "service_name": "AWS Glue",
        "usage_type": "USW2-GlueInteractiveSession-DPU-Hour",
        "unit": "DPU-Hour",
        "base_daily_cost": 64.0,
        "pricing_term": "Usage",
        "list_multiplier": 1.0,
        "resource_prefix": "arn:aws:glue:us-east-1:job/etl-batch",
    },
    {
        "product_code": "AmazonAthena",
        "service_name": "Amazon Athena",
        "usage_type": "USW2-DataScannedInTB",
        "unit": "TB",
        "base_daily_cost": 28.0,
        "pricing_term": "Usage",
        "list_multiplier": 1.0,
        "resource_prefix": "arn:aws:athena:us-east-1:workgroup/analytics",
    },
    {
        "product_code": "AmazonBedrock",
        "service_name": "Amazon Bedrock",
        "usage_type": "USE1-Claude3Sonnet-InputTokens",
        "unit": "1K-Tokens",
        "base_daily_cost": 82.0,
        "pricing_term": "Usage",
        "list_multiplier": 1.0,
        "resource_prefix": "arn:aws:bedrock:us-east-1::foundation-model/claude-3",
    },
    {
        "product_code": "AmazonVPC",
        "service_name": "Amazon Virtual Private Cloud (NAT Gateway)",
        "usage_type": "USE1-NatGateway-Bytes",
        "unit": "GB",
        "base_daily_cost": 55.0,
        "pricing_term": "Usage",
        "list_multiplier": 1.0,
        "resource_prefix": "arn:aws:ec2:us-east-1:natgateway/nat-0891bc2",
    },
    {
        "product_code": "AmazonRDS",
        "service_name": "Amazon Relational Database Service",
        "usage_type": "USE1-InstanceUsage:db.r6i.4xlarge",
        "unit": "Hrs",
        "base_daily_cost": 165.0,
        "pricing_term": "DiscountedUsage",
        "list_multiplier": 1.32,
        "resource_prefix": "arn:aws:rds:us-east-1:db:orders-primary",
    },
]


def generate_cur_dataset(
    days: int = 90,
    end_date: date | None = None,
    seed: int = 42,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Generate 90 days of realistic multi-account AWS CUR 2.0 line items with injected spikes & waste."""
    rng = random.Random(seed)
    target_end_date = end_date or date(2026, 3, 31)
    start_date = target_end_date - timedelta(days=days - 1)

    records: list[dict] = []
    line_item_counter = 100000

    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        billing_period = current_date.strftime("%Y-%m")
        usage_start_iso = f"{current_date.isoformat()}T00:00:00Z"
        usage_end_iso = f"{current_date.isoformat()}T23:59:59Z"

        # Mild organic growth across the 90-day window (+0.15% per day)
        trend_factor = 1.0 + (day_offset * 0.0015)
        # Mild weekday vs weekend seasonality
        weekend_factor = 0.92 if current_date.weekday() >= 5 else 1.03

        for acct_idx, acct in enumerate(WORKLOAD_ACCOUNTS):
            account_scale = 1.15 if acct_idx == 0 else (1.05 if acct_idx == 1 else 0.85)

            for svc in SERVICE_PROFILES:
                line_item_counter += 1
                noise = rng.uniform(0.93, 1.07)
                effective_cost = round(
                    svc["base_daily_cost"] * account_scale * trend_factor * weekend_factor * noise,
                    2,
                )

                # Default tag compliance: mostly compliant except deliberate untagged patterns
                tag_cost_center = acct["cost_center"]
                tag_environment = acct["environment"]
                tag_owner = acct["owner"]
                tag_project = acct["project"]
                resource_id = f"{svc['resource_prefix']}-{acct['account_id'][-4:]}"
                usage_type = svc["usage_type"]
                line_item_type = svc["pricing_term"]
                idle_waste_flag = False
                idle_reason = ""

                # --- Scenario 1: Runaway NAT Gateway Data Processing Spike (last 3 days in prod-core) ---
                if (
                    acct["account_name"] == "prod-core-workloads"
                    and svc["product_code"] == "AmazonVPC"
                    and day_offset >= days - 3
                ):
                    effective_cost = round(effective_cost * 4.85, 2)
                    resource_id = "arn:aws:ec2:us-east-1:111122223333:natgateway/nat-0runaway991"

                # --- Scenario 2: Uncontrolled Bedrock Token Burst & Untagged Shadow AI Jobs (last 4 days) ---
                if (
                    acct["account_name"] == "ai-ml-genai-sandbox"
                    and svc["product_code"] == "AmazonBedrock"
                    and day_offset >= days - 4
                ):
                    effective_cost = round(effective_cost * 3.9, 2)
                    tag_cost_center = ""
                    tag_owner = ""
                    resource_id = "arn:aws:bedrock:us-east-1:333344445555:agent/untagged-experiment-loop"

                # --- Scenario 3: Runaway Ad-Hoc Athena Full-Table Scans (day_offset == days - 2) ---
                if (
                    acct["account_name"] == "enterprise-data-platform"
                    and svc["product_code"] == "AmazonAthena"
                    and day_offset >= days - 2
                ):
                    effective_cost = round(effective_cost * 4.4, 2)
                    resource_id = "arn:aws:athena:us-east-1:222233334444:workgroup/adhoc-unpartitioned-scan"

                # --- Scenario 4: Untagged AWS Glue Interactive Sessions in ai-ml-genai-sandbox ---
                if (
                    acct["account_name"] == "ai-ml-genai-sandbox"
                    and svc["product_code"] == "AWSGlue"
                    and day_offset % 3 == 0
                ):
                    tag_cost_center = ""
                    tag_project = ""

                # --- Scenario 5: Oversized Low-CPU RDS Cluster in enterprise-data-platform (On-Demand waste) ---
                if (
                    acct["account_name"] == "enterprise-data-platform"
                    and svc["product_code"] == "AmazonRDS"
                ):
                    line_item_type = "Usage"  # Uncovered On-Demand instead of RI/Savings Plan
                    usage_type = "USE1-InstanceUsage:db.r6i.8xlarge"
                    idle_waste_flag = True
                    idle_reason = "Oversized db.r6i.8xlarge at <11% peak CPU utilization on uncovered On-Demand rate"

                billed_cost = round(effective_cost * 1.04, 2)
                list_cost = round(effective_cost * svc["list_multiplier"], 2)
                usage_amount = round(effective_cost / max(0.08, rng.uniform(0.45, 2.25)), 3)

                records.append(
                    {
                        "identity_line_item_id": f"cur2-li-{line_item_counter}",
                        "bill_payer_account_id": PAYER_ACCOUNT_ID,
                        "bill_billing_period_start_date": f"{billing_period}-01T00:00:00Z",
                        "line_item_usage_account_id": acct["account_id"],
                        "line_item_usage_account_name": acct["account_name"],
                        "line_item_usage_start_date": usage_start_iso,
                        "line_item_usage_end_date": usage_end_iso,
                        "line_item_product_code": svc["product_code"],
                        "product_service_name": svc["service_name"],
                        "line_item_usage_type": usage_type,
                        "line_item_line_item_type": line_item_type,
                        "line_item_resource_id": resource_id,
                        "product_region_code": "us-east-1",
                        "line_item_usage_amount": usage_amount,
                        "pricing_unit": svc["unit"],
                        "line_item_unblended_cost": billed_cost,
                        "reservation_effective_cost": effective_cost,
                        "pricing_public_on_demand_cost": list_cost,
                        "resource_tags_user_cost_center": tag_cost_center,
                        "resource_tags_user_environment": tag_environment,
                        "resource_tags_user_owner": tag_owner,
                        "resource_tags_user_project": tag_project,
                        "line_item_is_idle_waste": idle_waste_flag,
                        "line_item_idle_reason": idle_reason,
                    }
                )

            # --- Scenario 6: Unattached gp3 EBS Volumes (pure idle waste across accounts) ---
            if acct["account_name"] in ("prod-core-workloads", "ai-ml-genai-sandbox"):
                line_item_counter += 1
                ebs_daily_cost = 19.50 if acct["account_name"] == "prod-core-workloads" else 14.25
                records.append(
                    {
                        "identity_line_item_id": f"cur2-li-{line_item_counter}",
                        "bill_payer_account_id": PAYER_ACCOUNT_ID,
                        "bill_billing_period_start_date": f"{billing_period}-01T00:00:00Z",
                        "line_item_usage_account_id": acct["account_id"],
                        "line_item_usage_account_name": acct["account_name"],
                        "line_item_usage_start_date": usage_start_iso,
                        "line_item_usage_end_date": usage_end_iso,
                        "line_item_product_code": "AmazonEC2",
                        "product_service_name": "Amazon Elastic Block Store (Unattached EBS)",
                        "line_item_usage_type": "USE1-EBS:VolumeUsage.gp3",
                        "line_item_line_item_type": "Usage",
                        "line_item_resource_id": f"vol-0unattached{acct['account_id'][-4:]}",
                        "product_region_code": "us-east-1",
                        "line_item_usage_amount": 2048.0,
                        "pricing_unit": "GB-Mo",
                        "line_item_unblended_cost": ebs_daily_cost,
                        "reservation_effective_cost": ebs_daily_cost,
                        "pricing_public_on_demand_cost": ebs_daily_cost,
                        "resource_tags_user_cost_center": "",
                        "resource_tags_user_environment": acct["environment"],
                        "resource_tags_user_owner": "",
                        "resource_tags_user_project": "",
                        "line_item_is_idle_waste": True,
                        "line_item_idle_reason": "Unattached 2TB gp3 EBS volume in 'available' state for >30 days",
                    }
                )

    df = pd.DataFrame(records)

    base_dir = Path(output_dir or os.environ.get("LOCAL_DATA_DIR", "data"))
    raw_dir = base_dir / "raw_cur2"
    raw_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = raw_dir / "cur2_90d_export.parquet"
    df.to_parquet(parquet_path, index=False)

    return df


if __name__ == "__main__":
    dataset = generate_cur_dataset()
    print(
        f"[FinOps Simulator] Generated {len(dataset):,} CUR 2.0 billing records across "
        f"{dataset['line_item_usage_account_id'].nunique()} AWS accounts and "
        f"{dataset['line_item_product_code'].nunique()} services."
    )
