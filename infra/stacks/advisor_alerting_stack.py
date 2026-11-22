"""AdvisorAndAlertingStack: AWS Lambda + Amazon Bedrock FinOps Advisor and KMS-encrypted SNS alerts."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_lambda as _lambda,
)
from aws_cdk import (
    aws_sns as sns,
)
from constructs import Construct


class AdvisorAndAlertingStack(Stack):
    """Provisions the Amazon Bedrock FinOps Advisor Lambda and KMS-encrypted SNS alerting topic."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        kms_key: kms.IKey,
        anomalies_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.bedrock_model_id = "anthropic.claude-3-haiku-20240307-v1:0"

        # 1. KMS-Encrypted Amazon SNS Topic for Executive FinOps Briefs (Slack / Email integration)
        self.alerts_topic = sns.Topic(
            self,
            "FinOpsExecutiveAlertsTopic",
            topic_name="finops-executive-optimization-alerts",
            display_name="AWS FinOps & Spend Intelligence Executive Advisor",
            master_key=kms_key,
        )

        # 2. AWS Lambda + Amazon Bedrock FinOps Advisor
        self.bedrock_advisor_lambda = _lambda.Function(
            self,
            "BedrockFinOpsAdvisorLambda",
            function_name=f"finops-bedrock-executive-advisor-{cdk.Aws.REGION}",
            description="Synthesizes DynamoDB spend anomalies into plain-English executive briefs via Amazon Bedrock",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas.advisor_handler.lambda_handler",
            code=_lambda.Code.from_asset("src"),
            timeout=Duration.minutes(2),
            memory_size=512,
            environment={
                "DEMO_MODE": "false",
                "FINOPS_ANOMALIES_TABLE": anomalies_table.table_name,
                "FINOPS_BEDROCK_MODEL_ID": self.bedrock_model_id,
                "FINOPS_SNS_TOPIC_ARN": self.alerts_topic.topic_arn,
            },
        )

        anomalies_table.grant_read_write_data(self.bedrock_advisor_lambda)
        self.alerts_topic.grant_publish(self.bedrock_advisor_lambda)
        kms_key.grant_encrypt_decrypt(self.bedrock_advisor_lambda)

        # Least-privilege permission scoped specifically to Bedrock foundation models in the deployment region
        self.bedrock_advisor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                sid="InvokeBedrockFoundationModelForFinOpsBriefs",
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{cdk.Aws.REGION}::foundation-model/{self.bedrock_model_id}",
                    f"arn:aws:bedrock:{cdk.Aws.REGION}::foundation-model/amazon.nova-micro-v1:0",
                ],
            )
        )

        # 3. EventBridge Schedule to dispatch daily Executive Optimization Brief at 06:30 UTC
        self.advisor_schedule_rule = events.Rule(
            self,
            "FinOpsExecutiveAdvisorScheduleRule",
            rule_name="finops-daily-bedrock-executive-brief-trigger",
            description="Runs the Amazon Bedrock FinOps Advisor daily at 06:30 UTC after anomaly detection",
            schedule=events.Schedule.cron(minute="30", hour="6"),
        )
        self.advisor_schedule_rule.add_target(targets.LambdaFunction(self.bedrock_advisor_lambda))

        # 4. CloudFormation Outputs
        CfnOutput(
            self,
            "FinOpsAlertsSnsTopicArn",
            value=self.alerts_topic.topic_arn,
            description="SNS Topic ARN receiving Amazon Bedrock executive FinOps briefs",
        )
        CfnOutput(
            self,
            "BedrockAdvisorLambdaName",
            value=self.bedrock_advisor_lambda.function_name,
            description="Lambda function generating executive remediation summaries via Amazon Bedrock",
        )
        CfnOutput(
            self,
            "BedrockModelIdOutput",
            value=self.bedrock_model_id,
            description="Amazon Bedrock model ID configured for FinOps optimization briefs",
        )
