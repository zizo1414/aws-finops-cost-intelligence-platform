"""AWS Glue 4.0 PySpark ETL Script: Converts Raw AWS CUR 2.0 Exports into FinOps FOCUS 1.0 Parquet."""

from __future__ import annotations

import sys


def build_focus_spark_sql(source_view: str = "raw_cur2") -> str:
    """Return the Spark SQL transformation query mapping AWS CUR 2.0 into FinOps FOCUS 1.0."""
    return f"""
    SELECT
        bill_payer_account_id AS billing_account_id,
        line_item_usage_account_id AS sub_account_id,
        line_item_usage_account_name AS sub_account_name,
        line_item_usage_start_date AS charge_period_start,
        line_item_product_code AS service_name,
        CASE line_item_product_code
            WHEN 'AmazonEC2' THEN 'Compute'
            WHEN 'AmazonS3' THEN 'Storage'
            WHEN 'AWSGlue' THEN 'Analytics & ETL'
            WHEN 'AmazonAthena' THEN 'Analytics & ETL'
            WHEN 'AmazonBedrock' THEN 'AI and Machine Learning'
            WHEN 'AmazonVPC' THEN 'Networking'
            WHEN 'AmazonRDS' THEN 'Databases'
            ELSE 'Other'
        END AS service_category,
        line_item_resource_id AS resource_id,
        product_region_code AS region_id,
        CASE
            WHEN line_item_line_item_type IN ('Tax', 'Fee') THEN line_item_line_item_type
            ELSE 'Usage'
        END AS charge_category,
        CASE
            WHEN line_item_line_item_type IN ('SavingsPlanCoveredUsage', 'DiscountedUsage') THEN 'Committed'
            WHEN line_item_line_item_type = 'Spot' THEN 'Spot'
            ELSE 'On-Demand'
        END AS pricing_category,
        CAST(line_item_unblended_cost AS DOUBLE) AS billed_cost,
        CAST(reservation_effective_cost AS DOUBLE) AS effective_cost,
        CAST(pricing_public_on_demand_cost AS DOUBLE) AS list_cost,
        CAST(line_item_usage_amount AS DOUBLE) AS usage_quantity,
        pricing_unit AS usage_unit,
        CASE
            WHEN COALESCE(resource_tags_user_cost_center, '') != ''
             AND COALESCE(resource_tags_user_environment, '') != ''
             AND COALESCE(resource_tags_user_owner, '') != ''
             AND COALESCE(resource_tags_user_project, '') != ''
            THEN 'Compliant'
            ELSE 'Non-Compliant'
        END AS tag_compliance_status,
        CASE
            WHEN line_item_usage_account_id = '444455556666' THEN 'CC-9000-SHARED-PROPORTIONAL'
            WHEN COALESCE(resource_tags_user_cost_center, '') != '' THEN resource_tags_user_cost_center
            ELSE CONCAT('ACCOUNT-FALLBACK-', line_item_usage_account_id)
        END AS allocated_cost_center,
        CASE
            WHEN line_item_usage_account_id = '444455556666' THEN 'Proportional Shared Allocation'
            WHEN COALESCE(resource_tags_user_cost_center, '') != '' THEN 'Direct Tag Chargeback'
            ELSE 'Account-Level Fallback Showback'
        END AS chargeback_model,
        CAST(COALESCE(line_item_is_idle_waste, FALSE) AS BOOLEAN) AS is_idle_waste,
        SUBSTR(bill_billing_period_start_date, 1, 7) AS billing_period,
        line_item_usage_account_id AS linked_account_id
    FROM {source_view}
    """


def run_glue_etl() -> None:  # pragma: no cover - executed inside AWS Glue PySpark runtime
    """Entrypoint executed inside AWS Glue 4.0 PySpark cluster."""
    from awsglue.context import GlueContext
    from awsglue.job import Job
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext

    args = getResolvedOptions(
        sys.argv,
        ["JOB_NAME", "RAW_CUR_BUCKET", "FOCUS_CURATED_BUCKET", "CURATED_DATABASE"],
    )

    sc = SparkContext()
    glue_context = GlueContext(sc)
    spark = glue_context.spark_session
    job = Job(glue_context)
    job.init(args["JOB_NAME"], args)

    raw_s3_path = f"s3://{args['RAW_CUR_BUCKET']}/cur2/"
    curated_s3_path = f"s3://{args['FOCUS_CURATED_BUCKET']}/focus_v1/"

    raw_df = spark.read.parquet(raw_s3_path)
    raw_df.createOrReplaceTempView("raw_cur2")

    focus_df = spark.sql(build_focus_spark_sql("raw_cur2"))

    (
        focus_df.write.mode("overwrite")
        .format("parquet")
        .partitionBy("billing_period", "linked_account_id")
        .save(curated_s3_path)
    )

    job.commit()


if __name__ == "__main__":  # pragma: no cover
    run_glue_etl()
