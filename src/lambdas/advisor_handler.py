"""AWS Lambda Handler: Generates Executive FinOps Remediation Briefs via Amazon Bedrock and publishes to SNS."""

from __future__ import annotations

import os
from typing import Any

import boto3
import pandas as pd

from src.bedrock_advisor import invoke_bedrock_finops_advisor


def lambda_handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    anomalies_df: pd.DataFrame | None = None,
    waste_df: pd.DataFrame | None = None,
    bedrock_client: Any | None = None,
    sns_client: Any | None = None,
) -> dict[str, Any]:
    """Read spend anomalies, invoke Amazon Bedrock FinOps Advisor, and publish executive brief to SNS."""
    demo_mode = os.environ.get("DEMO_MODE", "true").lower() == "true"
    model_id = os.environ.get("FINOPS_BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    sns_topic_arn = os.environ.get("FINOPS_SNS_TOPIC_ARN", "")
    table_name = os.environ.get("FINOPS_ANOMALIES_TABLE", "FinOpsAnomaliesTable")

    if anomalies_df is None:
        if not demo_mode:
            ddb = boto3.resource("dynamodb")
            table = ddb.Table(table_name)
            items = table.scan().get("Items", [])
            anomalies_df = pd.DataFrame(items)
            if not anomalies_df.empty and "dollar_delta" in anomalies_df.columns:
                anomalies_df["dollar_delta"] = anomalies_df["dollar_delta"].astype(float)
        else:
            anomalies_df = pd.DataFrame()

    if waste_df is None:
        waste_df = pd.DataFrame()

    brief = invoke_bedrock_finops_advisor(
        anomalies_df=anomalies_df,
        waste_df=waste_df,
        demo_mode=demo_mode and bedrock_client is None,
        model_id=model_id,
        bedrock_client=bedrock_client,
    )

    sns_message_id = None
    if sns_topic_arn and (not demo_mode or sns_client is not None):
        publisher = sns_client or boto3.client("sns")
        pub_resp = publisher.publish(
            TopicArn=sns_topic_arn,
            Subject="AWS FinOps FOCUS Executive Optimization Brief",
            Message=brief["executive_summary_markdown"],
        )
        sns_message_id = pub_resp.get("MessageId")

    return {
        "status": "PUBLISHED" if sns_message_id else "GENERATED",
        "model_id": brief["model_id"],
        "projected_annual_savings_usd": brief["projected_annual_savings_usd"],
        "sns_message_id": sns_message_id,
        "brief": brief,
    }
