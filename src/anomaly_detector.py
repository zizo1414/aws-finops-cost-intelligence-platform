"""Deterministic Rolling 14-Day Spend Anomaly Detector (Z-Score + % Spike) & Idle Waste Analyzer."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd


@dataclass
class SpendAnomalyFinding:
    """Structured FinOps spend anomaly record compatible with Amazon DynamoDB and DuckDB."""

    anomaly_id: str
    usage_date: str
    sub_account_id: str
    sub_account_name: str
    service_name: str
    service_category: str
    resource_id: str
    actual_daily_spend: float
    rolling_14d_avg_spend: float
    rolling_14d_stddev: float
    dollar_delta: float
    pct_spike: float
    z_score: float
    severity: str
    tag_compliance_status: str
    allocated_cost_center: str
    ttl_epoch: int


def detect_spend_anomalies(
    focus_df: pd.DataFrame,
    z_score_threshold: float = 2.2,
    pct_spike_threshold: float = 30.0,
    min_dollar_delta: float = 25.0,
) -> pd.DataFrame:
    """Detect daily spend spikes per (SubAccountId, ServiceName) using a 14-day trailing baseline window."""
    con = duckdb.connect(":memory:")
    con.register("focus", focus_df)

    query = f"""
    WITH daily_service_spend AS (
        SELECT
            UsageDate,
            SubAccountId,
            SubAccountName,
            ServiceName,
            ServiceCategory,
            ARG_MAX(ResourceId, EffectiveCost) AS TopDriverResourceId,
            ARG_MAX(TagComplianceStatus, EffectiveCost) AS TagComplianceStatus,
            ARG_MAX(AllocatedCostCenter, EffectiveCost) AS AllocatedCostCenter,
            SUM(EffectiveCost) AS DailyEffectiveSpend
        FROM focus
        GROUP BY UsageDate, SubAccountId, SubAccountName, ServiceName, ServiceCategory
    ),
    rolling_stats AS (
        SELECT
            UsageDate,
            SubAccountId,
            SubAccountName,
            ServiceName,
            ServiceCategory,
            TopDriverResourceId,
            TagComplianceStatus,
            AllocatedCostCenter,
            DailyEffectiveSpend,
            AVG(DailyEffectiveSpend) OVER (
                PARTITION BY SubAccountId, ServiceName
                ORDER BY UsageDate
                ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
            ) AS Rolling14dAvg,
            STDDEV_SAMP(DailyEffectiveSpend) OVER (
                PARTITION BY SubAccountId, ServiceName
                ORDER BY UsageDate
                ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
            ) AS Rolling14dStdDev,
            COUNT(DailyEffectiveSpend) OVER (
                PARTITION BY SubAccountId, ServiceName
                ORDER BY UsageDate
                ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
            ) AS BaselineDays
        FROM daily_service_spend
    ),
    scored AS (
        SELECT
            UsageDate AS usage_date,
            SubAccountId AS sub_account_id,
            SubAccountName AS sub_account_name,
            ServiceName AS service_name,
            ServiceCategory AS service_category,
            TopDriverResourceId AS resource_id,
            TagComplianceStatus AS tag_compliance_status,
            AllocatedCostCenter AS allocated_cost_center,
            ROUND(DailyEffectiveSpend, 2) AS actual_daily_spend,
            ROUND(Rolling14dAvg, 2) AS rolling_14d_avg_spend,
            ROUND(COALESCE(Rolling14dStdDev, 1.0), 2) AS rolling_14d_stddev,
            ROUND(DailyEffectiveSpend - Rolling14dAvg, 2) AS dollar_delta,
            ROUND(((DailyEffectiveSpend - Rolling14dAvg) / NULLIF(Rolling14dAvg, 0)) * 100.0, 2) AS pct_spike,
            ROUND(
                (DailyEffectiveSpend - Rolling14dAvg) /
                GREATEST(COALESCE(Rolling14dStdDev, 1.0), 1.0),
                2
            ) AS z_score
        FROM rolling_stats
        WHERE BaselineDays >= 14
    )
    SELECT *
    FROM scored
    WHERE z_score >= {float(z_score_threshold)}
      AND pct_spike >= {float(pct_spike_threshold)}
      AND dollar_delta >= {float(min_dollar_delta)}
    ORDER BY usage_date DESC, dollar_delta DESC
    """
    raw_anomalies = con.execute(query).df()
    con.close()

    if raw_anomalies.empty:
        return pd.DataFrame(
            columns=[
                "anomaly_id",
                "usage_date",
                "sub_account_id",
                "sub_account_name",
                "service_name",
                "service_category",
                "resource_id",
                "actual_daily_spend",
                "rolling_14d_avg_spend",
                "rolling_14d_stddev",
                "dollar_delta",
                "pct_spike",
                "z_score",
                "severity",
                "tag_compliance_status",
                "allocated_cost_center",
                "ttl_epoch",
            ]
        )

    ttl_epoch = int((datetime.now(timezone.utc) + timedelta(days=90)).timestamp())
    findings: list[dict] = []

    for row in raw_anomalies.to_dict(orient="records"):
        key_str = f"{row['usage_date']}:{row['sub_account_id']}:{row['service_name']}"
        anomaly_id = "anom-" + hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:12]
        if row["z_score"] >= 5.0 or row["dollar_delta"] >= 200.0:
            severity = "CRITICAL"
        elif row["z_score"] >= 3.2 or row["dollar_delta"] >= 100.0:
            severity = "HIGH"
        else:
            severity = "MEDIUM"

        finding = SpendAnomalyFinding(
            anomaly_id=anomaly_id,
            usage_date=str(row["usage_date"]),
            sub_account_id=str(row["sub_account_id"]),
            sub_account_name=str(row["sub_account_name"]),
            service_name=str(row["service_name"]),
            service_category=str(row["service_category"]),
            resource_id=str(row["resource_id"]),
            actual_daily_spend=float(row["actual_daily_spend"]),
            rolling_14d_avg_spend=float(row["rolling_14d_avg_spend"]),
            rolling_14d_stddev=float(row["rolling_14d_stddev"]),
            dollar_delta=float(row["dollar_delta"]),
            pct_spike=float(row["pct_spike"]),
            z_score=float(row["z_score"]),
            severity=severity,
            tag_compliance_status=str(row["tag_compliance_status"]),
            allocated_cost_center=str(row["allocated_cost_center"]),
            ttl_epoch=ttl_epoch,
        )
        findings.append(asdict(finding))

    return pd.DataFrame(findings)


def summarize_idle_and_untagged_waste(focus_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate idle resource waste and untagged non-compliant spend across the 90-day window."""
    con = duckdb.connect(":memory:")
    con.register("focus", focus_df)

    query = """
    SELECT
        SubAccountId AS sub_account_id,
        SubAccountName AS sub_account_name,
        ServiceName AS service_name,
        ResourceId AS resource_id,
        PricingCategory AS pricing_category,
        TagComplianceStatus AS tag_compliance_status,
        IsIdleWaste AS is_idle_waste,
        IdleWasteReason AS idle_waste_reason,
        COUNT(DISTINCT UsageDate) AS active_days,
        ROUND(SUM(EffectiveCost), 2) AS total_effective_spend,
        ROUND(
            CASE
                WHEN IsIdleWaste AND ServiceName = 'AmazonEC2' THEN SUM(EffectiveCost) * 1.0
                WHEN IsIdleWaste AND ServiceName = 'AmazonRDS' THEN SUM(EffectiveCost) * 0.52
                WHEN TagComplianceStatus = 'Non-Compliant' THEN SUM(EffectiveCost) * 0.25
                ELSE 0.0
            END,
            2
        ) AS estimated_recoverable_savings
    FROM focus
    WHERE IsIdleWaste = TRUE OR TagComplianceStatus = 'Non-Compliant'
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
    ORDER BY estimated_recoverable_savings DESC, total_effective_spend DESC
    """
    waste_df = con.execute(query).df()
    con.close()
    return waste_df
