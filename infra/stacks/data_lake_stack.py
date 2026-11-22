"""FinOpsDataLakeStack: KMS-encrypted S3 buckets, Glue Data Catalog, and Athena Workgroup."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_athena as athena,
)
from aws_cdk import (
    aws_glue as glue,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct


class FinOpsDataLakeStack(Stack):
    """Provisions the FinOps FOCUS data lake storage, Glue databases/tables, and Athena workgroup."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Customer-Managed KMS Key for S3, Glue, Athena, DynamoDB, and SNS encryption
        self.kms_key = kms.Key(
            self,
            "FinOpsLakehouseKmsKey",
            alias="alias/finops-cost-intelligence-lakehouse",
            description="KMS key encrypting FinOps CUR 2.0 raw exports, FOCUS curated tables, and Athena results",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # 2. Raw AWS CUR 2.0 Exports Bucket (KMS encrypted, SSL enforced, lifecycle tiered)
        self.raw_cur_bucket = s3.Bucket(
            self,
            "RawCurExportsBucket",
            bucket_name=f"finops-raw-cur2-{cdk.Aws.ACCOUNT_ID}-{cdk.Aws.REGION}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.kms_key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="TransitionRawCurToIntelligentTieringAndGlacier",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=Duration.days(30),
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(180),
                        ),
                    ],
                )
            ],
        )

        # 3. Curated FinOps FOCUS 1.0 Normalized Parquet/Iceberg Bucket
        self.focus_curated_bucket = s3.Bucket(
            self,
            "FocusCuratedBucket",
            bucket_name=f"finops-focus-curated-{cdk.Aws.ACCOUNT_ID}-{cdk.Aws.REGION}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.kms_key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="OptimizeFocusParquetStorage",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=Duration.days(90),
                        )
                    ],
                )
            ],
        )

        # 4. Athena Query Results Bucket (Auto-expire query artifacts after 14 days)
        self.athena_results_bucket = s3.Bucket(
            self,
            "AthenaQueryResultsBucket",
            bucket_name=f"finops-athena-results-{cdk.Aws.ACCOUNT_ID}-{cdk.Aws.REGION}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.kms_key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireAthenaScratchResults",
                    enabled=True,
                    expiration=Duration.days(14),
                )
            ],
        )

        # 5. AWS Glue Data Catalog Databases: finops_raw and finops_focus_curated
        self.raw_database_name = "finops_raw"
        self.curated_database_name = "finops_focus_curated"

        self.raw_glue_db = glue.CfnDatabase(
            self,
            "FinOpsRawGlueDatabase",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.raw_database_name,
                description="Raw AWS Data Exports (CUR 2.0) landing database",
                location_uri=f"s3://{self.raw_cur_bucket.bucket_name}/cur2/",
            ),
        )

        self.curated_glue_db = glue.CfnDatabase(
            self,
            "FinOpsFocusCuratedGlueDatabase",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.curated_database_name,
                description="FinOps FOCUS 1.0 normalized cost and usage lakehouse database",
                location_uri=f"s3://{self.focus_curated_bucket.bucket_name}/focus_v1/",
            ),
        )

        # 6. Glue Catalog Table for FOCUS 1.0 Curated Dataset (partitioned by billing_period & linked_account_id)
        self.focus_glue_table = glue.CfnTable(
            self,
            "FocusCuratedGlueTable",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_name=self.curated_database_name,
            table_input=glue.CfnTable.TableInputProperty(
                name="focus_cost_and_usage",
                description="FinOps FOCUS 1.0 normalized table partitioned by billing_period and linked_account_id",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "parquet",
                    "compressionType": "snappy",
                    "focus_spec_version": "1.0",
                },
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="billing_period", type="string"),
                    glue.CfnTable.ColumnProperty(name="linked_account_id", type="string"),
                ],
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=f"s3://{self.focus_curated_bucket.bucket_name}/focus_v1/",
                    input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                        parameters={"serialization.format": "1"},
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name="charge_period_start", type="string"),
                        glue.CfnTable.ColumnProperty(name="billing_account_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="sub_account_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="sub_account_name", type="string"),
                        glue.CfnTable.ColumnProperty(name="service_name", type="string"),
                        glue.CfnTable.ColumnProperty(name="service_category", type="string"),
                        glue.CfnTable.ColumnProperty(name="resource_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="region_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="charge_category", type="string"),
                        glue.CfnTable.ColumnProperty(name="pricing_category", type="string"),
                        glue.CfnTable.ColumnProperty(name="billed_cost", type="double"),
                        glue.CfnTable.ColumnProperty(name="effective_cost", type="double"),
                        glue.CfnTable.ColumnProperty(name="list_cost", type="double"),
                        glue.CfnTable.ColumnProperty(name="usage_quantity", type="double"),
                        glue.CfnTable.ColumnProperty(name="usage_unit", type="string"),
                        glue.CfnTable.ColumnProperty(name="tag_compliance_status", type="string"),
                        glue.CfnTable.ColumnProperty(name="allocated_cost_center", type="string"),
                        glue.CfnTable.ColumnProperty(name="chargeback_model", type="string"),
                        glue.CfnTable.ColumnProperty(name="is_idle_waste", type="boolean"),
                    ],
                ),
            ),
        )
        self.focus_glue_table.add_dependency(self.curated_glue_db)

        # 7. Amazon Athena Workgroup with SSE-KMS result encryption and 10 GB per-query byte-scan guardrail
        self.athena_workgroup_name = "finops_cost_intelligence_wg"
        self.athena_workgroup = athena.CfnWorkGroup(
            self,
            "FinOpsAthenaWorkGroup",
            name=self.athena_workgroup_name,
            description="FinOps Cost Intelligence Athena Workgroup with KMS encryption and 10GB scan cutoff",
            recursive_delete_option=True,
            state="ENABLED",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                bytes_scanned_cutoff_per_query=10 * 1024 * 1024 * 1024,  # 10 GB guardrail
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{self.athena_results_bucket.bucket_name}/results/",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_KMS",
                        kms_key=self.kms_key.key_arn,
                    ),
                ),
            ),
        )

        # 8. CloudFormation Outputs for scripts/export_env.py
        CfnOutput(
            self,
            "RawCurBucketName",
            value=self.raw_cur_bucket.bucket_name,
            description="S3 bucket storing raw multi-account AWS CUR 2.0 exports",
        )
        CfnOutput(
            self,
            "FocusCuratedBucketName",
            value=self.focus_curated_bucket.bucket_name,
            description="S3 bucket storing FOCUS 1.0 normalized Parquet tables",
        )
        CfnOutput(
            self,
            "AthenaResultsBucketName",
            value=self.athena_results_bucket.bucket_name,
            description="S3 bucket storing encrypted Athena query results",
        )
        CfnOutput(
            self,
            "GlueRawDatabaseName",
            value=self.raw_database_name,
            description="AWS Glue database for raw CUR 2.0 data",
        )
        CfnOutput(
            self,
            "GlueCuratedDatabaseName",
            value=self.curated_database_name,
            description="AWS Glue database for FinOps FOCUS 1.0 curated tables",
        )
        CfnOutput(
            self,
            "AthenaWorkGroupName",
            value=self.athena_workgroup_name,
            description="Amazon Athena Workgroup with byte-scan cost guardrails",
        )
        CfnOutput(
            self,
            "FinOpsKmsKeyArn",
            value=self.kms_key.key_arn,
            description="Customer-managed KMS Key ARN for the FinOps data lake",
        )
