#!/usr/bin/env python3
"""AWS CDK Entrypoint for the Cloud FinOps & Cost Intelligence Platform (FOCUS 1.0 Lakehouse)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import aws_cdk as cdk

# Ensure repository root is importable when invoked via `cdk synth`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from infra.stacks.advisor_alerting_stack import AdvisorAndAlertingStack
from infra.stacks.data_lake_stack import FinOpsDataLakeStack
from infra.stacks.transformation_anomaly_stack import TransformationAndAnomalyStack


def build_app() -> cdk.App:
    """Instantiate the CDK App and wire all three modular FinOps stacks."""
    app = cdk.App()

    env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1")),
    )

    data_lake_stack = FinOpsDataLakeStack(
        app,
        "FinOpsDataLakeStack",
        env=env,
        description="FinOps FOCUS 1.0 Lakehouse: KMS-encrypted S3 buckets, Glue Databases, and Athena Workgroup",
    )

    transformation_stack = TransformationAndAnomalyStack(
        app,
        "TransformationAndAnomalyStack",
        kms_key=data_lake_stack.kms_key,
        raw_cur_bucket=data_lake_stack.raw_cur_bucket,
        focus_curated_bucket=data_lake_stack.focus_curated_bucket,
        athena_results_bucket=data_lake_stack.athena_results_bucket,
        curated_database_name=data_lake_stack.curated_database_name,
        athena_workgroup_name=data_lake_stack.athena_workgroup_name,
        env=env,
        description="FinOps CUR 2.0 -> FOCUS Glue ETL, Step Functions + EventBridge pipeline, and DynamoDB anomalies",
    )
    transformation_stack.add_dependency(data_lake_stack)

    advisor_stack = AdvisorAndAlertingStack(
        app,
        "AdvisorAndAlertingStack",
        kms_key=data_lake_stack.kms_key,
        anomalies_table=transformation_stack.anomalies_table,
        env=env,
        description="Amazon Bedrock FinOps Executive Advisor Lambda and KMS-encrypted SNS alerts",
    )
    advisor_stack.add_dependency(transformation_stack)

    # Apply standard FinOps governance tags across all CDK resources
    for tag_key, tag_value in {
        "Project": "aws-finops-cost-intelligence-platform",
        "CostCenter": "CC-1000-FINOPS",
        "Environment": "Production",
        "ManagedBy": "AWS-CDK",
        "FinOpsStandard": "FOCUS-1.0",
    }.items():
        cdk.Tags.of(app).add(tag_key, tag_value)

    return app


if __name__ == "__main__":
    cdk_app = build_app()
    cdk_app.synth()
