#!/usr/bin/env python3
"""Reads deployed CloudFormation stack outputs and auto-populates the local .env file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import boto3

STACK_NAMES = [
    "FinOpsDataLakeStack",
    "TransformationAndAnomalyStack",
    "AdvisorAndAlertingStack",
]

OUTPUT_TO_ENV_MAP = {
    "RawCurBucketName": "FINOPS_RAW_CUR_BUCKET",
    "FocusCuratedBucketName": "FINOPS_FOCUS_CURATED_BUCKET",
    "AthenaResultsBucketName": "FINOPS_ATHENA_RESULTS_BUCKET",
    "GlueRawDatabaseName": "FINOPS_GLUE_RAW_DB",
    "GlueCuratedDatabaseName": "FINOPS_GLUE_CURATED_DB",
    "AthenaWorkGroupName": "FINOPS_ATHENA_WORKGROUP",
    "FinOpsKmsKeyArn": "FINOPS_KMS_KEY_ARN",
    "GlueJobNameOutput": "FINOPS_GLUE_JOB_NAME",
    "AnomaliesTableNameOutput": "FINOPS_ANOMALIES_TABLE",
    "StepFunctionArnOutput": "FINOPS_STEP_FUNCTION_ARN",
    "FinOpsAlertsSnsTopicArn": "FINOPS_SNS_TOPIC_ARN",
    "BedrockModelIdOutput": "FINOPS_BEDROCK_MODEL_ID",
}


def collect_stack_outputs(cfn_client: Any | None = None) -> dict[str, str]:
    """Query AWS CloudFormation for outputs across all three FinOps stacks."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = cfn_client or boto3.client("cloudformation", region_name=region)
    env_values: dict[str, str] = {"DEMO_MODE": "false", "AWS_REGION": region}

    for stack_name in STACK_NAMES:
        resp = client.describe_stacks(StackName=stack_name)
        stacks = resp.get("Stacks", [])
        if not stacks:
            continue
        for output in stacks[0].get("Outputs", []):
            key = output.get("OutputKey", "")
            val = output.get("OutputValue", "")
            if key in OUTPUT_TO_ENV_MAP:
                env_values[OUTPUT_TO_ENV_MAP[key]] = val

    return env_values


def write_env_file(env_updates: dict[str, str], env_path: str | Path = ".env") -> Path:
    """Merge discovered CloudFormation outputs into .env (creating from .env.example if missing)."""
    target = Path(env_path)
    example = Path(".env.example")

    if not target.exists() and example.exists():
        lines = example.read_text(encoding="utf-8").splitlines()
    elif target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    updated_keys: set[str] = set()
    output_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output_lines.append(line)
            continue
        k, _ = stripped.split("=", 1)
        k = k.strip()
        if k in env_updates:
            output_lines.append(f"{k}={env_updates[k]}")
            updated_keys.add(k)
        else:
            output_lines.append(line)

    for k, v in env_updates.items():
        if k not in updated_keys:
            output_lines.append(f"{k}={v}")

    target.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":  # pragma: no cover
    discovered = collect_stack_outputs()
    path = write_env_file(discovered)
    print(f"[FinOps Env Exporter] Exported {len(discovered)} settings to {path}")
