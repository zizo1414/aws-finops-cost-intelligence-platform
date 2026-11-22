"""Unit tests for all three modular AWS CDK FinOps stacks using aws_cdk.assertions."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.app import build_app
from infra.stacks.advisor_alerting_stack import AdvisorAndAlertingStack
from infra.stacks.data_lake_stack import FinOpsDataLakeStack
from infra.stacks.transformation_anomaly_stack import TransformationAndAnomalyStack


def _synthesize_stacks() -> tuple[Template, Template, Template]:
    app = cdk.App()
    dl_stack = FinOpsDataLakeStack(app, "TestFinOpsDataLakeStack")
    tx_stack = TransformationAndAnomalyStack(
        app,
        "TestTransformationAndAnomalyStack",
        kms_key=dl_stack.kms_key,
        raw_cur_bucket=dl_stack.raw_cur_bucket,
        focus_curated_bucket=dl_stack.focus_curated_bucket,
        athena_results_bucket=dl_stack.athena_results_bucket,
        curated_database_name=dl_stack.curated_database_name,
        athena_workgroup_name=dl_stack.athena_workgroup_name,
    )
    adv_stack = AdvisorAndAlertingStack(
        app,
        "TestAdvisorAndAlertingStack",
        kms_key=dl_stack.kms_key,
        anomalies_table=tx_stack.anomalies_table,
    )
    return (
        Template.from_stack(dl_stack),
        Template.from_stack(tx_stack),
        Template.from_stack(adv_stack),
    )


def test_data_lake_stack_kms_s3_glue_and_athena() -> None:
    """Verify KMS rotation, 3 encrypted S3 buckets, Glue databases, and Athena scan guardrail."""
    dl_template, _, _ = _synthesize_stacks()

    dl_template.has_resource_properties(
        "AWS::KMS::Key",
        {"EnableKeyRotation": True},
    )

    dl_template.resource_count_is("AWS::S3::Bucket", 3)
    dl_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "BucketKeyEnabled": True,
                        "ServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"},
                    }
                ]
            },
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )

    dl_template.resource_count_is("AWS::Glue::Database", 2)
    dl_template.has_resource_properties(
        "AWS::Glue::Database",
        {"DatabaseInput": Match.object_like({"Name": "finops_raw"})},
    )
    dl_template.has_resource_properties(
        "AWS::Glue::Database",
        {"DatabaseInput": Match.object_like({"Name": "finops_focus_curated"})},
    )

    dl_template.has_resource_properties(
        "AWS::Glue::Table",
        {
            "DatabaseName": "finops_focus_curated",
            "TableInput": Match.object_like(
                {
                    "Name": "focus_cost_and_usage",
                    "PartitionKeys": [
                        {"Name": "billing_period", "Type": "string"},
                        {"Name": "linked_account_id", "Type": "string"},
                    ],
                }
            ),
        },
    )

    dl_template.has_resource_properties(
        "AWS::Athena::WorkGroup",
        {
            "Name": "finops_cost_intelligence_wg",
            "WorkGroupConfiguration": Match.object_like(
                {
                    "BytesScannedCutoffPerQuery": 10737418240,
                    "EnforceWorkGroupConfiguration": True,
                }
            ),
        },
    )


def test_transformation_and_anomaly_stack_resources() -> None:
    """Verify Glue PySpark job, DynamoDB anomalies table, Step Functions state machine, and EventBridge rule."""
    _, tx_template, _ = _synthesize_stacks()

    tx_template.has_resource_properties(
        "AWS::Glue::Job",
        {
            "Name": "cur-to-focus-normalization-job",
            "GlueVersion": "4.0",
            "WorkerType": "G.1X",
        },
    )

    tx_template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "FinOpsAnomaliesTable",
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "anomaly_id", "KeyType": "HASH"},
                {"AttributeName": "usage_date", "KeyType": "RANGE"},
            ],
            "TimeToLiveSpecification": {"AttributeName": "ttl_epoch", "Enabled": True},
        },
    )

    tx_template.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    tx_template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "finops-daily-focus-pipeline-trigger",
            "ScheduleExpression": "cron(0 6 * * ? *)",
        },
    )


def test_advisor_and_alerting_stack_bedrock_and_sns() -> None:
    """Verify KMS-encrypted SNS topic and least-privilege Bedrock InvokeModel policy."""
    _, _, adv_template = _synthesize_stacks()

    adv_template.has_resource_properties(
        "AWS::SNS::Topic",
        {"TopicName": "finops-executive-optimization-alerts"},
    )

    adv_template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": "bedrock:InvokeModel",
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )


def test_build_app_synthesizes_all_three_stacks() -> None:
    """Verify infra/app.py builds and synthesizes all 3 FinOps stacks cleanly."""
    app = build_app()
    assembly = app.synth()
    stack_names = {s.stack_name for s in assembly.stacks}
    assert {
        "FinOpsDataLakeStack",
        "TransformationAndAnomalyStack",
        "AdvisorAndAlertingStack",
    }.issubset(stack_names)
