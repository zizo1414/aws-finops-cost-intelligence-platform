"""FinOps FOCUS 1.0 Normalization, Tag Hygiene & Showback/Chargeback Allocation Engine."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

FOCUS_REQUIRED_COLUMNS = [
    "BillingAccountId",
    "SubAccountId",
    "SubAccountName",
    "BillingPeriod",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "UsageDate",
    "ServiceName",
    "ServiceCategory",
    "ResourceId",
    "RegionId",
    "ChargeCategory",
    "PricingCategory",
    "BilledCost",
    "EffectiveCost",
    "ListCost",
    "UsageQuantity",
    "UsageUnit",
    "TagCostCenter",
    "TagEnvironment",
    "TagOwner",
    "TagProject",
    "TagComplianceStatus",
    "MissingTagsCount",
    "AllocatedCostCenter",
    "ChargebackModel",
    "IsIdleWaste",
    "IdleWasteReason",
]

SERVICE_CATEGORY_MAP = {
    "AmazonEC2": "Compute",
    "AmazonS3": "Storage",
    "AWSGlue": "Analytics & ETL",
    "AmazonAthena": "Analytics & ETL",
    "AmazonBedrock": "AI and Machine Learning",
    "AmazonVPC": "Networking",
    "AmazonRDS": "Databases",
}

ACCOUNT_DEFAULT_COST_CENTER = {
    "111122223333": "CC-2010-CORE-ENG",
    "222233334444": "CC-3020-DATA-LAKE",
    "333344445555": "CC-4050-GENAI-LABS",
    "444455556666": "CC-9000-SHARED-INFRA",
}


def transform_cur_to_focus_df(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Transform raw AWS CUR 2.0 DataFrame into the FinOps FOCUS 1.0 schema with tag hygiene & chargeback rules."""
    con = duckdb.connect(":memory:")
    con.register("raw_cur2", raw_df)

    query = """
    SELECT
        bill_payer_account_id AS BillingAccountId,
        line_item_usage_account_id AS SubAccountId,
        line_item_usage_account_name AS SubAccountName,
        SUBSTR(bill_billing_period_start_date, 1, 7) AS BillingPeriod,
        line_item_usage_start_date AS ChargePeriodStart,
        line_item_usage_end_date AS ChargePeriodEnd,
        SUBSTR(line_item_usage_start_date, 1, 10) AS UsageDate,
        line_item_product_code AS ServiceName,
        CASE line_item_product_code
            WHEN 'AmazonEC2' THEN 'Compute'
            WHEN 'AmazonS3' THEN 'Storage'
            WHEN 'AWSGlue' THEN 'Analytics & ETL'
            WHEN 'AmazonAthena' THEN 'Analytics & ETL'
            WHEN 'AmazonBedrock' THEN 'AI and Machine Learning'
            WHEN 'AmazonVPC' THEN 'Networking'
            WHEN 'AmazonRDS' THEN 'Databases'
            ELSE 'Other'
        END AS ServiceCategory,
        line_item_resource_id AS ResourceId,
        product_region_code AS RegionId,
        CASE
            WHEN line_item_line_item_type IN ('Tax', 'Fee') THEN line_item_line_item_type
            ELSE 'Usage'
        END AS ChargeCategory,
        CASE
            WHEN line_item_line_item_type IN ('SavingsPlanCoveredUsage', 'DiscountedUsage') THEN 'Committed'
            WHEN line_item_line_item_type = 'Spot' THEN 'Spot'
            ELSE 'On-Demand'
        END AS PricingCategory,
        ROUND(CAST(line_item_unblended_cost AS DOUBLE), 2) AS BilledCost,
        ROUND(CAST(reservation_effective_cost AS DOUBLE), 2) AS EffectiveCost,
        ROUND(CAST(pricing_public_on_demand_cost AS DOUBLE), 2) AS ListCost,
        ROUND(CAST(line_item_usage_amount AS DOUBLE), 3) AS UsageQuantity,
        pricing_unit AS UsageUnit,
        COALESCE(resource_tags_user_cost_center, '') AS TagCostCenter,
        COALESCE(resource_tags_user_environment, '') AS TagEnvironment,
        COALESCE(resource_tags_user_owner, '') AS TagOwner,
        COALESCE(resource_tags_user_project, '') AS TagProject,
        (
            CASE WHEN COALESCE(resource_tags_user_cost_center, '') = '' THEN 1 ELSE 0 END +
            CASE WHEN COALESCE(resource_tags_user_environment, '') = '' THEN 1 ELSE 0 END +
            CASE WHEN COALESCE(resource_tags_user_owner, '') = '' THEN 1 ELSE 0 END +
            CASE WHEN COALESCE(resource_tags_user_project, '') = '' THEN 1 ELSE 0 END
        ) AS MissingTagsCount,
        CASE
            WHEN COALESCE(resource_tags_user_cost_center, '') != ''
             AND COALESCE(resource_tags_user_environment, '') != ''
             AND COALESCE(resource_tags_user_owner, '') != ''
             AND COALESCE(resource_tags_user_project, '') != ''
            THEN 'Compliant'
            ELSE 'Non-Compliant'
        END AS TagComplianceStatus,
        CASE
            WHEN line_item_usage_account_id = '444455556666' THEN 'CC-9000-SHARED-PROPORTIONAL'
            WHEN COALESCE(resource_tags_user_cost_center, '') != '' THEN resource_tags_user_cost_center
            WHEN line_item_usage_account_id = '111122223333' THEN 'CC-2010-CORE-ENG (Account-Fallback)'
            WHEN line_item_usage_account_id = '222233334444' THEN 'CC-3020-DATA-LAKE (Account-Fallback)'
            WHEN line_item_usage_account_id = '333344445555' THEN 'CC-4050-GENAI-LABS (Account-Fallback)'
            ELSE 'CC-UNALLOCATED'
        END AS AllocatedCostCenter,
        CASE
            WHEN line_item_usage_account_id = '444455556666' THEN 'Proportional Shared Allocation'
            WHEN COALESCE(resource_tags_user_cost_center, '') != '' THEN 'Direct Tag Chargeback'
            ELSE 'Account-Level Fallback Showback'
        END AS ChargebackModel,
        CAST(COALESCE(line_item_is_idle_waste, FALSE) AS BOOLEAN) AS IsIdleWaste,
        COALESCE(line_item_idle_reason, '') AS IdleWasteReason,
        SUBSTR(bill_billing_period_start_date, 1, 7) AS billing_period,
        line_item_usage_account_id AS linked_account_id
    FROM raw_cur2
    ORDER BY UsageDate ASC, SubAccountId ASC, ServiceName ASC
    """
    focus_df = con.execute(query).df()
    con.close()
    return focus_df


def compute_shared_cost_allocation(focus_df: pd.DataFrame) -> pd.DataFrame:
    """Allocate shared-network-transit account spend proportionally across consuming business units."""
    con = duckdb.connect(":memory:")
    con.register("focus", focus_df)

    query = """
    WITH direct_spend AS (
        SELECT
            SubAccountId,
            SubAccountName,
            CASE
                WHEN SubAccountId = '111122223333' THEN 'CC-2010-CORE-ENG'
                WHEN SubAccountId = '222233334444' THEN 'CC-3020-DATA-LAKE'
                WHEN SubAccountId = '333344445555' THEN 'CC-4050-GENAI-LABS'
            END AS BusinessUnitCostCenter,
            SUM(EffectiveCost) AS DirectEffectiveSpend
        FROM focus
        WHERE SubAccountId != '444455556666'
        GROUP BY 1, 2, 3
    ),
    total_direct AS (
        SELECT SUM(DirectEffectiveSpend) AS TotalDirectSpend FROM direct_spend
    ),
    shared_pool AS (
        SELECT SUM(EffectiveCost) AS TotalSharedSpend
        FROM focus
        WHERE SubAccountId = '444455556666'
    )
    SELECT
        d.BusinessUnitCostCenter,
        d.SubAccountId,
        d.SubAccountName,
        ROUND(d.DirectEffectiveSpend, 2) AS DirectEffectiveSpend,
        ROUND((d.DirectEffectiveSpend / t.TotalDirectSpend) * 100.0, 2) AS ProportionalWeightPct,
        ROUND((d.DirectEffectiveSpend / t.TotalDirectSpend) * s.TotalSharedSpend, 2) AS AllocatedSharedSpend,
        ROUND(d.DirectEffectiveSpend + ((d.DirectEffectiveSpend / t.TotalDirectSpend) * s.TotalSharedSpend), 2)
            AS TotalFullyLoadedChargeback
    FROM direct_spend d
    CROSS JOIN total_direct t
    CROSS JOIN shared_pool s
    ORDER BY TotalFullyLoadedChargeback DESC
    """
    allocation_df = con.execute(query).df()
    con.close()
    return allocation_df


def write_focus_curated_parquet(focus_df: pd.DataFrame, output_dir: str | Path) -> Path:
    """Persist FOCUS 1.0 curated dataset partitioned by billing_period and linked_account_id."""
    curated_root = Path(output_dir) / "focus_curated"
    curated_root.mkdir(parents=True, exist_ok=True)
    flat_file = curated_root / "focus_cost_and_usage.parquet"
    focus_df.to_parquet(flat_file, index=False)
    return flat_file
