"""AWS Lambda Handler: Runs rolling 14-day spend anomaly detection and persists findings to DynamoDB."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import boto3
import pandas as pd

from src.anomaly_detector import detect_spend_anomalies


def _to_dynamodb_item(record: dict[str, Any]) -> dict[str, Any]:
    """Convert float fields in an anomaly dict into DynamoDB-compatible Decimal objects."""
    converted: dict[str, Any] = {}
    for key, val in record.items():
        if isinstance(val, float):
            converted[key] = Decimal(str(round(val, 4)))
        else:
            converted[key] = val
    return converted


def persist_anomalies_to_dynamodb(
    anomalies_df: pd.DataFrame,
    table_name: str,
    dynamodb_resource: Any | None = None,
) -> int:
    """Batch-write detected spend anomaly records into Amazon DynamoDB."""
    if anomalies_df.empty:
        return 0

    ddb = dynamodb_resource or boto3.resource("dynamodb")
    table = ddb.Table(table_name)

    written = 0
    with table.batch_writer() as batch:
        for row in anomalies_df.to_dict(orient="records"):
            batch.put_item(Item=_to_dynamodb_item(row))
            written += 1
    return written


def lambda_handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    focus_df: pd.DataFrame | None = None,
    dynamodb_resource: Any | None = None,
) -> dict[str, Any]:
    """AWS Lambda entrypoint invoked by Step Functions after the CUR -> FOCUS Glue ETL job."""
    table_name = os.environ.get("FINOPS_ANOMALIES_TABLE", "FinOpsAnomaliesTable")
    z_threshold = float(os.environ.get("FINOPS_ANOMALY_ZSCORE_THRESHOLD", "2.2"))
    pct_threshold = float(os.environ.get("FINOPS_ANOMALY_PCT_SPIKE_THRESHOLD", "30.0"))
    min_delta = float(os.environ.get("FINOPS_ANOMALY_MIN_DOLLAR_DELTA", "25.0"))

    if focus_df is None:
        local_curated_path = os.path.join(
            os.environ.get("LOCAL_DATA_DIR", "data"),
            "focus_curated",
            "focus_cost_and_usage.parquet",
        )
        focus_df = pd.read_parquet(local_curated_path)

    anomalies_df = detect_spend_anomalies(
        focus_df,
        z_score_threshold=z_threshold,
        pct_spike_threshold=pct_threshold,
        min_dollar_delta=min_delta,
    )

    demo_mode = os.environ.get("DEMO_MODE", "true").lower() == "true"
    records_written = 0
    if not demo_mode or dynamodb_resource is not None:
        records_written = persist_anomalies_to_dynamodb(
            anomalies_df,
            table_name=table_name,
            dynamodb_resource=dynamodb_resource,
        )

    return {
        "status": "SUCCEEDED",
        "anomalies_detected": int(len(anomalies_df)),
        "dynamodb_records_written": records_written,
        "top_anomalies": anomalies_df.head(5).to_dict(orient="records"),
    }
