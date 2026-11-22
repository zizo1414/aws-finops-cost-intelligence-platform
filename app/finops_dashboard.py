"""Streamlit Executive Cloud FinOps & Spend Intelligence Console (Dual Mode: DuckDB Local / Live AWS)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.anomaly_detector import detect_spend_anomalies, summarize_idle_and_untagged_waste
from src.bedrock_advisor import invoke_bedrock_finops_advisor
from src.focus_transformer import compute_shared_cost_allocation
from src.local_runner import run_local_finops_pipeline

load_dotenv()


def is_demo_mode() -> bool:
    """Return True when running in 100% offline DuckDB mode (default)."""
    return os.environ.get("DEMO_MODE", "true").lower() == "true"


def load_local_finops_data(data_dir: str = "data") -> dict[str, Any]:
    """Load FOCUS 1.0 dataset, chargeback allocations, anomalies, and Bedrock brief from local DuckDB."""
    base_dir = Path(data_dir)
    duckdb_path = base_dir / "finops_lakehouse.duckdb"
    if not duckdb_path.exists():
        run_local_finops_pipeline(base_dir)

    con = duckdb.connect(str(duckdb_path), read_only=True)
    focus_df = con.execute("SELECT * FROM focus_cost_and_usage").df()
    chargeback_df = con.execute("SELECT * FROM chargeback_allocation").df()
    anomalies_df = con.execute("SELECT * FROM spend_anomalies").df()
    waste_df = con.execute("SELECT * FROM idle_and_untagged_waste").df()
    con.close()

    brief = invoke_bedrock_finops_advisor(anomalies_df, waste_df, demo_mode=True)
    return {
        "focus_df": focus_df,
        "chargeback_df": chargeback_df,
        "anomalies_df": anomalies_df,
        "waste_df": waste_df,
        "brief": brief,
    }


def query_live_aws_athena_and_dynamodb() -> dict[str, Any]:
    """Query live Amazon Athena FOCUS tables and DynamoDB anomalies table when DEMO_MODE=false."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    database = os.environ.get("FINOPS_GLUE_CURATED_DB", "finops_focus_curated")
    workgroup = os.environ.get("FINOPS_ATHENA_WORKGROUP", "finops_cost_intelligence_wg")
    output_bucket = os.environ.get("FINOPS_ATHENA_RESULTS_BUCKET", "")
    anomalies_table_name = os.environ.get("FINOPS_ANOMALIES_TABLE", "FinOpsAnomaliesTable")
    model_id = os.environ.get("FINOPS_BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

    athena = boto3.client("athena", region_name=region)
    query_str = f"""
    SELECT
        billing_account_id AS BillingAccountId,
        sub_account_id AS SubAccountId,
        sub_account_name AS SubAccountName,
        billing_period AS BillingPeriod,
        charge_period_start AS ChargePeriodStart,
        SUBSTR(charge_period_start, 1, 10) AS UsageDate,
        service_name AS ServiceName,
        service_category AS ServiceCategory,
        resource_id AS ResourceId,
        region_id AS RegionId,
        charge_category AS ChargeCategory,
        pricing_category AS PricingCategory,
        billed_cost AS BilledCost,
        effective_cost AS EffectiveCost,
        list_cost AS ListCost,
        usage_quantity AS UsageQuantity,
        usage_unit AS UsageUnit,
        tag_compliance_status AS TagComplianceStatus,
        allocated_cost_center AS AllocatedCostCenter,
        chargeback_model AS ChargebackModel,
        is_idle_waste AS IsIdleWaste,
        '' AS IdleWasteReason
    FROM {database}.focus_cost_and_usage
    LIMIT 10000
    """
    exec_params: dict[str, Any] = {
        "QueryString": query_str,
        "QueryExecutionContext": {"Database": database},
        "WorkGroup": workgroup,
    }
    if output_bucket:
        exec_params["ResultConfiguration"] = {"OutputLocation": f"s3://{output_bucket}/results/"}

    start_resp = athena.start_query_execution(**exec_params)
    query_execution_id = start_resp["QueryExecutionId"]

    for _ in range(30):
        status_resp = athena.get_query_execution(QueryExecutionId=query_execution_id)
        state = status_resp["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    results = athena.get_query_results(QueryExecutionId=query_execution_id)
    rows = results.get("ResultSet", {}).get("Rows", [])
    headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]] if rows else []
    records = []
    for r in rows[1:]:
        vals = [c.get("VarCharValue", "") for c in r.get("Data", [])]
        records.append(dict(zip(headers, vals)))

    focus_df = pd.DataFrame(records)
    for numeric_col in ("BilledCost", "EffectiveCost", "ListCost", "UsageQuantity"):
        if numeric_col in focus_df.columns:
            focus_df[numeric_col] = pd.to_numeric(focus_df[numeric_col], errors="coerce").fillna(0.0)
    if "IsIdleWaste" in focus_df.columns:
        focus_df["IsIdleWaste"] = focus_df["IsIdleWaste"].astype(str).str.lower() == "true"

    ddb = boto3.resource("dynamodb", region_name=region)
    ddb_items = ddb.Table(anomalies_table_name).scan().get("Items", [])
    anomalies_df = pd.DataFrame(ddb_items) if ddb_items else detect_spend_anomalies(focus_df)
    chargeback_df = compute_shared_cost_allocation(focus_df)
    waste_df = summarize_idle_and_untagged_waste(focus_df)
    brief = invoke_bedrock_finops_advisor(
        anomalies_df, waste_df, demo_mode=False, model_id=model_id
    )

    return {
        "focus_df": focus_df,
        "chargeback_df": chargeback_df,
        "anomalies_df": anomalies_df,
        "waste_df": waste_df,
        "brief": brief,
    }


def compute_executive_kpis(focus_df: pd.DataFrame, brief: dict[str, Any]) -> dict[str, float]:
    """Calculate Executive FinOps KPIs (MTD Spend, Forecasted Monthly Spend, Tag Compliance %, Savings)."""
    latest_period = str(focus_df["BillingPeriod"].max())
    mtd_df = focus_df[focus_df["BillingPeriod"] == latest_period]
    mtd_spend = float(mtd_df["EffectiveCost"].sum())
    active_days_in_month = max(int(mtd_df["UsageDate"].nunique()), 1)
    forecasted_monthly_spend = round((mtd_spend / active_days_in_month) * 31.0, 2)

    total_90d_spend = float(focus_df["EffectiveCost"].sum())
    compliant_90d_spend = float(
        focus_df.loc[focus_df["TagComplianceStatus"] == "Compliant", "EffectiveCost"].sum()
    )
    tag_compliance_pct = round((compliant_90d_spend / max(total_90d_spend, 1.0)) * 100.0, 2)

    committed_90d_spend = float(
        focus_df.loc[focus_df["PricingCategory"] == "Committed", "EffectiveCost"].sum()
    )
    esr_discount_saved = float(focus_df["ListCost"].sum() - focus_df["EffectiveCost"].sum())

    return {
        "latest_billing_period": latest_period,
        "mtd_effective_spend": round(mtd_spend, 2),
        "forecasted_monthly_spend": forecasted_monthly_spend,
        "tag_compliance_pct": tag_compliance_pct,
        "annualized_savings_potential": float(brief["projected_annual_savings_usd"]),
        "commitment_coverage_pct": round((committed_90d_spend / max(total_90d_spend, 1.0)) * 100.0, 2),
        "Negotiated_and_SP_Savings_90d": round(esr_discount_saved, 2),
    }


def render_dashboard() -> None:  # pragma: no cover - UI layout function
    """Render the Streamlit Executive FinOps & Cost Intelligence Console."""
    st.set_page_config(
        page_title="AWS FinOps & Cost Intelligence Platform (FOCUS 1.0)",
        page_icon="📊",
        layout="wide",
    )

    demo = is_demo_mode()
    mode_badge = (
        "🟢 DEMO_MODE=true (100% Local DuckDB FOCUS 1.0 Lakehouse)"
        if demo
        else "🔵 DEMO_MODE=false (Live AWS Athena + DynamoDB + Bedrock)"
    )

    st.title("☁️ Enterprise Cloud FinOps & Cost Intelligence Lakehouse")
    st.caption(
        f"**FinOps FOCUS 1.0 Open Specification** | Multi-Account CUR 2.0 Analytics | {mode_badge}"
    )

    data = load_local_finops_data() if demo else query_live_aws_athena_and_dynamodb()
    focus_df: pd.DataFrame = data["focus_df"]
    chargeback_df: pd.DataFrame = data["chargeback_df"]
    anomalies_df: pd.DataFrame = data["anomalies_df"]
    waste_df: pd.DataFrame = data["waste_df"]
    brief: dict[str, Any] = data["brief"]

    kpis = compute_executive_kpis(focus_df, brief)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        f"MTD Effective Spend ({kpis['latest_billing_period']})",
        f"${kpis['mtd_effective_spend']:,.2f}",
        f"Commitment Coverage: {kpis['commitment_coverage_pct']}%",
    )
    col2.metric(
        "Forecasted Monthly Run-Rate",
        f"${kpis['forecasted_monthly_spend']:,.2f}",
        f"${kpis['Negotiated_and_SP_Savings_90d']:,.0f} SP/RI Savings (90d)",
    )
    col3.metric(
        "Tag Governance Compliance",
        f"{kpis['tag_compliance_pct']}%",
        "Target: >= 95.0% Cost Allocation",
    )
    col4.metric(
        "Identified Annualized Savings",
        f"${kpis['annualized_savings_potential']:,.2f}/yr",
        f"{len(anomalies_df)} Spend Spikes Flagged",
    )

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "📈 FOCUS Spend & Showback/Chargeback",
            "🚨 Deterministic Spend Anomalies (14d Z-Score)",
            "🏷️ Tag Hygiene & Idle Resource Waste",
            "🤖 Amazon Bedrock Executive FinOps Advisor",
        ]
    )

    with tab1:
        st.subheader("Daily Effective Spend Trend by FOCUS Service Category")
        daily_cat = (
            focus_df.groupby(["UsageDate", "ServiceCategory"])["EffectiveCost"]
            .sum()
            .unstack(fill_value=0.0)
        )
        st.area_chart(daily_cat)

        c_left, c_right = st.columns(2)
        with c_left:
            st.subheader("Multi-Account Chargeback & Shared Transit Allocation")
            st.dataframe(chargeback_df, use_container_width=True, hide_index=True)
        with c_right:
            st.subheader("FOCUS Pricing Category Breakdown (90 Days)")
            pricing_summary = (
                focus_df.groupby(["ServiceName", "PricingCategory"])["EffectiveCost"]
                .sum()
                .reset_index()
                .sort_values("EffectiveCost", ascending=False)
            )
            st.dataframe(pricing_summary, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Rolling 14-Day Z-Score & % Spend Spike Alerts")
        st.markdown(
            "Anomalies are flagged deterministically when `z_score >= 2.2`, `pct_spike >= 30%`, "
            "and `dollar_delta >= $25/day` over the trailing 14-day service/account baseline."
        )
        st.dataframe(anomalies_df, use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("Idle Resources & Non-Compliant Untagged Spend Inventory")
        st.dataframe(waste_df, use_container_width=True, hide_index=True)

    with tab4:
        st.markdown(brief["executive_summary_markdown"])
        st.subheader("Structured Engineering Remediation Actions")
        st.dataframe(
            pd.DataFrame(brief["recommendations"]),
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":  # pragma: no cover
    render_dashboard()
