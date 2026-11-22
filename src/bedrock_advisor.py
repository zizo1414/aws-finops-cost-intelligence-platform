"""Amazon Bedrock FinOps Executive Advisor: Prompts, Bedrock Runtime client, and offline simulator."""

from __future__ import annotations

import json
from typing import Any

import boto3
import pandas as pd


def build_bedrock_finops_prompt(
    anomalies: list[dict[str, Any]],
    idle_waste: list[dict[str, Any]],
) -> str:
    """Build a structured FinOps FOCUS executive optimization prompt for Amazon Bedrock."""
    prompt_payload = {
        "role": "Principal AWS FinOps Architect",
        "standard": "FinOps FOCUS 1.0",
        "top_anomalies": anomalies[:5],
        "top_idle_or_untagged_waste": idle_waste[:5],
    }
    return (
        "You are a Principal AWS Solutions Architect specializing in Cloud FinOps and Cost Optimization.\n"
        "Analyze the following FinOps FOCUS 1.0 spend anomalies and idle/untagged resource findings.\n"
        "Produce a concise, actionable Executive FinOps Brief containing:\n"
        "1. Executive Spend Anomaly Summary (top dollar spikes, z-scores, and root-cause resources).\n"
        "2. Immediate Remediation Actions (VPC Gateway Endpoints for NAT Gateway spikes, "
        "RDS Rightsizing + Savings Plans, Unattached EBS cleanup, S3 Intelligent-Tiering, "
        "and Athena workgroup scan guardrails).\n"
        "3. Tag Governance & Chargeback Enforcement recommendations.\n\n"
        f"FINDINGS_JSON:\n{json.dumps(prompt_payload, indent=2)}"
    )


def generate_simulated_executive_brief(
    anomalies_df: pd.DataFrame,
    waste_df: pd.DataFrame,
) -> dict[str, Any]:
    """Generate a deterministic, production-grade executive remediation brief for DEMO_MODE=true."""
    total_anomaly_delta = float(anomalies_df["dollar_delta"].sum()) if not anomalies_df.empty else 0.0
    total_recoverable = (
        float(waste_df["estimated_recoverable_savings"].sum()) if not waste_df.empty else 0.0
    )
    annualized_savings = round((total_recoverable / 90.0) * 365.0 + (total_anomaly_delta * 12.0), 2)

    recommendations = [
        {
            "category": "Networking Optimization (VPC Endpoints)",
            "target_service": "AmazonVPC (NAT Gateway)",
            "target_account": "prod-core-workloads (111122223333)",
            "resource_id": "arn:aws:ec2:us-east-1:111122223333:natgateway/nat-0runaway991",
            "finding": "Daily NAT Gateway spend spiked +385% (Z-Score > 8.0) due to high-volume S3 and ECR traffic.",
            "action": (
                "Deploy a free Gateway VPC Endpoint for Amazon S3 and Interface VPC Endpoints for ECR "
                "to eliminate $0.045/GB NAT data processing fees."
            ),
            "estimated_monthly_savings_usd": 1420.00,
        },
        {
            "category": "Database Rightsizing & Commitment Discounts",
            "target_service": "AmazonRDS",
            "target_account": "enterprise-data-platform (222233334444)",
            "resource_id": "arn:aws:rds:us-east-1:db:orders-primary-4444",
            "finding": "Oversized db.r6i.8xlarge running at <11% peak CPU on uncovered On-Demand pricing.",
            "action": (
                "Rightsize instance to db.r6g.2xlarge (Graviton3) and apply a 1-Year No-Upfront RDS "
                "Reserved Instance for 52% combined effective cost reduction."
            ),
            "estimated_monthly_savings_usd": 2680.00,
        },
        {
            "category": "GenAI Token Guardrails & Tag Governance",
            "target_service": "AmazonBedrock",
            "target_account": "ai-ml-genai-sandbox (333344445555)",
            "resource_id": "arn:aws:bedrock:us-east-1:333344445555:agent/untagged-experiment-loop",
            "finding": "Untagged Bedrock agent triggered a +290% token spike lacking CostCenter and Owner tags.",
            "action": (
                "Route high-volume classification tasks to Claude 3 Haiku / Amazon Nova Micro, enable "
                "Prompt Caching, and enforce AWS Organizations Tag Policies + SCPs blocking untagged agents."
            ),
            "estimated_monthly_savings_usd": 940.00,
        },
        {
            "category": "Storage Hygiene (Unattached EBS & S3 Intelligent-Tiering)",
            "target_service": "AmazonEC2 (EBS) & AmazonS3",
            "target_account": "prod-core-workloads & ai-ml-genai-sandbox",
            "resource_id": "vol-0unattached3333 / vol-0unattached5555",
            "finding": "4 TB of unattached gp3 EBS volumes sitting idle in 'available' state for >30 days.",
            "action": (
                "Create final EBS snapshots via AWS Data Lifecycle Manager, delete unattached volumes, "
                "and transition S3 datasets >30 days old to S3 Intelligent-Tiering."
            ),
            "estimated_monthly_savings_usd": 1012.50,
        },
        {
            "category": "Analytics Query Guardrails",
            "target_service": "AmazonAthena",
            "target_account": "enterprise-data-platform (222233334444)",
            "resource_id": "arn:aws:athena:us-east-1:222233334444:workgroup/adhoc-unpartitioned-scan",
            "finding": "Ad-hoc queries without partition pruning caused a +340% daily Athena scan spike.",
            "action": (
                "Enforce the 10 GB BytesScannedCutoffPerQuery workgroup guardrail and require partition filters "
                "on billing_period and linked_account_id."
            ),
            "estimated_monthly_savings_usd": 465.00,
        },
    ]

    summary_markdown = (
        "### Executive FinOps Optimization Brief (Amazon Bedrock Advisor)\n"
        f"- **Detected Spend Spikes (90d Window):** `{len(anomalies_df)}` statistically significant anomalies "
        f"(`+${total_anomaly_delta:,.2f}` cumulative excess spend).\n"
        f"- **90-Day Recoverable Idle & Untagged Waste:** `${total_recoverable:,.2f}`\n"
        f"- **Projected Annualized Savings Opportunity:** **`${annualized_savings:,.2f}/year`**\n\n"
        "#### Priority Remediation Playbook\n"
        "1. **NAT Gateway Traffic Offload (`prod-core-workloads`)**: Provision S3 Gateway VPC Endpoint immediately "
        "to stop the +385% NAT data processing spike.\n"
        "2. **RDS Rightsizing + Graviton Migration (`enterprise-data-platform`)**: Downsize `db.r6i.8xlarge` to "
        "`db.r6g.2xlarge` and cover with a 1-yr Reserved Instance.\n"
        "3. **Shadow GenAI & Tag Policy Enforcement (`ai-ml-genai-sandbox`)**: Terminate untagged recursive "
        "Bedrock agent loops and enforce mandatory `CostCenter` and `Owner` tags.\n"
        "4. **Automated Storage Cleanup**: Snapshot and delete unattached `gp3` EBS volumes (`vol-0unattached*`)."
    )

    return {
        "model_id": "anthropic.claude-3-haiku-20240307-v1:0 (Simulated Offline Mode)",
        "anomaly_count": int(len(anomalies_df)),
        "total_anomaly_excess_usd": round(total_anomaly_delta, 2),
        "recoverable_waste_90d_usd": round(total_recoverable, 2),
        "projected_annual_savings_usd": annualized_savings,
        "executive_summary_markdown": summary_markdown,
        "recommendations": recommendations,
    }


def invoke_bedrock_finops_advisor(
    anomalies_df: pd.DataFrame,
    waste_df: pd.DataFrame,
    *,
    demo_mode: bool = True,
    model_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    bedrock_client: Any | None = None,
) -> dict[str, Any]:
    """Generate an executive FinOps optimization brief via local simulation or Amazon Bedrock Runtime."""
    base_brief = generate_simulated_executive_brief(anomalies_df, waste_df)
    if demo_mode:
        return base_brief

    prompt = build_bedrock_finops_prompt(
        anomalies=anomalies_df.to_dict(orient="records"),
        idle_waste=waste_df.to_dict(orient="records"),
    )

    client = bedrock_client or boto3.client("bedrock-runtime")
    request_body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 900,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=request_body,
    )
    raw_body = response["body"].read()
    parsed = json.loads(raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else raw_body)
    text_blocks = parsed.get("content", [])
    llm_summary = (
        text_blocks[0].get("text", base_brief["executive_summary_markdown"])
        if text_blocks
        else base_brief["executive_summary_markdown"]
    )

    base_brief["model_id"] = model_id
    base_brief["executive_summary_markdown"] = llm_summary
    return base_brief
