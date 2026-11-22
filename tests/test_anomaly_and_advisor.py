"""Tests for deterministic 14-day rolling z-score anomaly detection, Bedrock Advisor, and env exporter."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

from scripts.export_env import collect_stack_outputs, write_env_file
from scripts.generate_cur_data import generate_cur_dataset
from src.anomaly_detector import detect_spend_anomalies, summarize_idle_and_untagged_waste
from src.focus_transformer import transform_cur_to_focus_df
from src.lambdas.advisor_handler import lambda_handler as advisor_lambda_handler
from src.lambdas.anomaly_handler import lambda_handler as anomaly_lambda_handler


def test_rolling_14d_anomaly_detection_captures_injected_spikes(tmp_path: Path) -> None:
    """Verify rolling 14-day z-score detector flags NAT Gateway, Bedrock, and Athena spikes."""
    raw_df = generate_cur_dataset(days=90, output_dir=tmp_path)
    focus_df = transform_cur_to_focus_df(raw_df)

    anomalies_df = detect_spend_anomalies(focus_df)
    assert not anomalies_df.empty
    flagged_services = set(anomalies_df["service_name"].unique())
    assert {"AmazonVPC", "AmazonBedrock", "AmazonAthena"}.issubset(flagged_services)
    assert (anomalies_df["z_score"] >= 2.2).all()
    assert (anomalies_df["pct_spike"] >= 30.0).all()

    waste_df = summarize_idle_and_untagged_waste(focus_df)
    assert not waste_df.empty
    assert (waste_df["estimated_recoverable_savings"] >= 0).all()


def test_anomaly_lambda_handler_writes_to_mocked_dynamodb(tmp_path: Path) -> None:
    """Verify anomaly Lambda handler converts floats to Decimal and batch-writes to DynamoDB."""
    raw_df = generate_cur_dataset(days=45, output_dir=tmp_path)
    focus_df = transform_cur_to_focus_df(raw_df)

    mock_ddb = MagicMock()
    mock_table = MagicMock()
    mock_batch = MagicMock()
    mock_ddb.Table.return_value = mock_table
    mock_table.batch_writer.return_value.__enter__.return_value = mock_batch

    result = anomaly_lambda_handler({}, None, focus_df=focus_df, dynamodb_resource=mock_ddb)
    assert result["status"] == "SUCCEEDED"
    assert result["anomalies_detected"] > 0
    assert result["dynamodb_records_written"] == result["anomalies_detected"]
    assert mock_batch.put_item.call_count == result["anomalies_detected"]


def test_advisor_lambda_handler_with_mocked_bedrock_and_sns(tmp_path: Path, monkeypatch) -> None:
    """Verify Bedrock Advisor Lambda invokes Bedrock Runtime and publishes brief to SNS."""
    raw_df = generate_cur_dataset(days=45, output_dir=tmp_path)
    focus_df = transform_cur_to_focus_df(raw_df)
    anomalies_df = detect_spend_anomalies(focus_df)
    waste_df = summarize_idle_and_untagged_waste(focus_df)

    monkeypatch.setenv("FINOPS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:111122223333:finops-alerts")

    mock_bedrock = MagicMock()
    bedrock_payload = {
        "content": [
            {
                "type": "text",
                "text": "### Mocked Bedrock FinOps Brief\n- Deploy S3 Gateway VPC Endpoint.",
            }
        ]
    }
    mock_bedrock.invoke_model.return_value = {
        "body": io.BytesIO(json.dumps(bedrock_payload).encode("utf-8"))
    }

    mock_sns = MagicMock()
    mock_sns.publish.return_value = {"MessageId": "msg-finops-12345"}

    resp = advisor_lambda_handler(
        {},
        None,
        anomalies_df=anomalies_df,
        waste_df=waste_df,
        bedrock_client=mock_bedrock,
        sns_client=mock_sns,
    )
    assert resp["status"] == "PUBLISHED"
    assert resp["sns_message_id"] == "msg-finops-12345"
    assert "Mocked Bedrock FinOps Brief" in resp["brief"]["executive_summary_markdown"]
    mock_bedrock.invoke_model.assert_called_once()
    mock_sns.publish.assert_called_once()


def test_export_env_from_cloudformation_outputs(tmp_path: Path) -> None:
    """Verify scripts/export_env.py maps CloudFormation stack outputs into .env."""
    mock_cfn = MagicMock()
    mock_cfn.describe_stacks.side_effect = [
        {
            "Stacks": [
                {
                    "Outputs": [
                        {"OutputKey": "RawCurBucketName", "OutputValue": "finops-raw-cur2-test"},
                        {"OutputKey": "FocusCuratedBucketName", "OutputValue": "finops-focus-test"},
                    ]
                }
            ]
        },
        {
            "Stacks": [
                {
                    "Outputs": [
                        {"OutputKey": "AnomaliesTableNameOutput", "OutputValue": "FinOpsAnomaliesTable"}
                    ]
                }
            ]
        },
        {
            "Stacks": [
                {
                    "Outputs": [
                        {
                            "OutputKey": "FinOpsAlertsSnsTopicArn",
                            "OutputValue": "arn:aws:sns:us-east-1:123:alerts",
                        }
                    ]
                }
            ]
        },
    ]

    env_map = collect_stack_outputs(cfn_client=mock_cfn)
    assert env_map["FINOPS_RAW_CUR_BUCKET"] == "finops-raw-cur2-test"
    assert env_map["FINOPS_FOCUS_CURATED_BUCKET"] == "finops-focus-test"
    assert env_map["FINOPS_ANOMALIES_TABLE"] == "FinOpsAnomaliesTable"
    assert env_map["FINOPS_SNS_TOPIC_ARN"] == "arn:aws:sns:us-east-1:123:alerts"

    env_file = tmp_path / ".env"
    write_env_file(env_map, env_path=env_file)
    content = env_file.read_text(encoding="utf-8")
    assert "FINOPS_RAW_CUR_BUCKET=finops-raw-cur2-test" in content
    assert "DEMO_MODE=false" in content
