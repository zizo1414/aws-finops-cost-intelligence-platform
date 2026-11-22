"""TransformationAndAnomalyStack: AWS Glue FOCUS ETL, Step Functions + EventBridge, and DynamoDB anomalies."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
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
    aws_glue as glue,
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
    aws_s3 as s3,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct


class TransformationAndAnomalyStack(Stack):
    """Provisions the CUR 2.0 -> FOCUS Glue ETL job, daily Step Functions pipeline, and DynamoDB anomaly store."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        kms_key: kms.IKey,
        raw_cur_bucket: s3.IBucket,
        focus_curated_bucket: s3.IBucket,
        athena_results_bucket: s3.IBucket,
        curated_database_name: str,
        athena_workgroup_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Amazon DynamoDB Table for detected daily spend anomalies & idle resource findings
        self.anomalies_table = dynamodb.Table(
            self,
            "FinOpsAnomaliesTable",
            table_name="FinOpsAnomaliesTable",
            partition_key=dynamodb.Attribute(
                name="anomaly_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="usage_date",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="ttl_epoch",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # 2. Least-Privilege IAM Role for AWS Glue PySpark CUR -> FOCUS ETL Job
        self.glue_job_role = iam.Role(
            self,
            "CurToFocusGlueJobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            description="Least-privilege IAM role for AWS Glue CUR 2.0 to FinOps FOCUS 1.0 normalization job",
        )
        raw_cur_bucket.grant_read(self.glue_job_role)
        focus_curated_bucket.grant_read_write(self.glue_job_role)
        kms_key.grant_encrypt_decrypt(self.glue_job_role)

        self.glue_job_role.add_to_policy(
            iam.PolicyStatement(
                sid="GlueDataCatalogAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetPartitions",
                    "glue:BatchCreatePartition",
                    "glue:UpdateTable",
                ],
                resources=[
                    f"arn:aws:glue:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:catalog",
                    f"arn:aws:glue:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:database/{curated_database_name}",
                    f"arn:aws:glue:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:table/{curated_database_name}/*",
                ],
            )
        )
        self.glue_job_role.add_to_policy(
            iam.PolicyStatement(
                sid="GlueCloudWatchLogs",
                effect=iam.Effect.ALLOW,
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    f"arn:aws:logs:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:log-group:/aws-glue/jobs/*"
                ],
            )
        )

        # 3. AWS Glue (PySpark) ETL Job converting raw CUR 2.0 into FinOps FOCUS 1.0 + Tag Hygiene
        self.glue_job_name = "cur-to-focus-normalization-job"
        self.glue_job = glue.CfnJob(
            self,
            "CurToFocusGlueJob",
            name=self.glue_job_name,
            description="Converts raw AWS CUR 2.0 Parquet exports into FinOps FOCUS 1.0 schema with tag compliance",
            role=self.glue_job_role.role_arn,
            glue_version="4.0",
            worker_type="G.1X",
            number_of_workers=2,
            timeout=30,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                python_version="3",
                script_location=f"s3://{focus_curated_bucket.bucket_name}/scripts/cur_to_focus_job.py",
            ),
            default_arguments={
                "--job-language": "python",
                "--enable-metrics": "true",
                "--enable-continuous-cloudwatch-log": "true",
                "--enable-glue-datacatalog": "true",
                "--RAW_CUR_BUCKET": raw_cur_bucket.bucket_name,
                "--FOCUS_CURATED_BUCKET": focus_curated_bucket.bucket_name,
                "--CURATED_DATABASE": curated_database_name,
                "--REQUIRED_COST_TAGS": "CostCenter,Environment,Owner,Project",
            },
        )

        # 4. AWS Lambda for Deterministic Spend Anomaly Detection (Rolling 14-day Z-Score & % Spike)
        self.anomaly_detector_lambda = _lambda.Function(
            self,
            "SpendAnomalyDetectorLambda",
            function_name=f"finops-spend-anomaly-detector-{cdk.Aws.REGION}",
            description="Runs rolling 14-day z-score and % spike anomaly detection on FOCUS data into DynamoDB",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas.anomaly_handler.lambda_handler",
            code=_lambda.Code.from_asset("src"),
            timeout=Duration.minutes(3),
            memory_size=512,
            environment={
                "DEMO_MODE": "false",
                "FINOPS_ANOMALIES_TABLE": self.anomalies_table.table_name,
                "FINOPS_GLUE_CURATED_DB": curated_database_name,
                "FINOPS_ATHENA_WORKGROUP": athena_workgroup_name,
                "FINOPS_ATHENA_RESULTS_BUCKET": athena_results_bucket.bucket_name,
                "FINOPS_ANOMALY_ZSCORE_THRESHOLD": "2.2",
                "FINOPS_ANOMALY_PCT_SPIKE_THRESHOLD": "30.0",
                "FINOPS_ANOMALY_MIN_DOLLAR_DELTA": "25.0",
            },
        )

        self.anomalies_table.grant_read_write_data(self.anomaly_detector_lambda)
        focus_curated_bucket.grant_read(self.anomaly_detector_lambda)
        athena_results_bucket.grant_read_write(self.anomaly_detector_lambda)
        kms_key.grant_encrypt_decrypt(self.anomaly_detector_lambda)

        self.anomaly_detector_lambda.add_to_role_policy(
            iam.PolicyStatement(
                sid="AthenaAndGlueReadForAnomalyDetection",
                effect=iam.Effect.ALLOW,
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetPartitions",
                ],
                resources=[
                    f"arn:aws:athena:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:workgroup/{athena_workgroup_name}",
                    f"arn:aws:glue:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:catalog",
                    f"arn:aws:glue:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:database/{curated_database_name}",
                    f"arn:aws:glue:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:table/{curated_database_name}/*",
                ],
            )
        )

        # 5. AWS Step Functions Daily Orchestration State Machine
        run_glue_etl_task = tasks.GlueStartJobRun(
            self,
            "RunCurToFocusGlueETL",
            glue_job_name=self.glue_job_name,
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            result_path="$.glueResult",
        )

        run_anomaly_detection_task = tasks.LambdaInvoke(
            self,
            "ExecuteSpendAnomalyDetection",
            lambda_function=self.anomaly_detector_lambda,
            payload_response_only=True,
            result_path="$.anomalyResult",
        )

        pipeline_definition = run_glue_etl_task.next(run_anomaly_detection_task)

        self.state_machine = sfn.StateMachine(
            self,
            "FinOpsDailyIntelligenceStateMachine",
            state_machine_name="finops-daily-focus-and-anomaly-pipeline",
            definition_body=sfn.DefinitionBody.from_chainable(pipeline_definition),
            timeout=Duration.minutes(45),
            tracing_enabled=True,
        )

        # 6. Amazon EventBridge Daily Trigger (06:00 UTC after AWS CUR 2.0 refresh)
        self.daily_schedule_rule = events.Rule(
            self,
            "FinOpsDailyPipelineScheduleRule",
            rule_name="finops-daily-focus-pipeline-trigger",
            description="Triggers the FinOps CUR 2.0 -> FOCUS ETL and anomaly detection pipeline daily at 06:00 UTC",
            schedule=events.Schedule.cron(minute="0", hour="6"),
        )
        self.daily_schedule_rule.add_target(targets.SfnStateMachine(self.state_machine))

        # 7. CloudFormation Outputs
        CfnOutput(
            self,
            "GlueJobNameOutput",
            value=self.glue_job_name,
            description="AWS Glue PySpark job normalizing CUR 2.0 into FinOps FOCUS 1.0",
        )
        CfnOutput(
            self,
            "AnomaliesTableNameOutput",
            value=self.anomalies_table.table_name,
            description="DynamoDB table storing daily spend anomalies and idle waste findings",
        )
        CfnOutput(
            self,
            "StepFunctionArnOutput",
            value=self.state_machine.state_machine_arn,
            description="Step Functions state machine ARN orchestrating daily FinOps ETL and anomaly detection",
        )
