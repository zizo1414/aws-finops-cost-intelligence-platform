"""Tests for synthetic CUR 2.0 generation, FinOps FOCUS 1.0 schema normalization, and chargeback rules."""

from __future__ import annotations

from pathlib import Path

from app.finops_dashboard import compute_executive_kpis, load_local_finops_data
from scripts.generate_cur_data import generate_cur_dataset
from src.focus_transformer import (
    FOCUS_REQUIRED_COLUMNS,
    compute_shared_cost_allocation,
    transform_cur_to_focus_df,
)
from src.glue_jobs.cur_to_focus_job import build_focus_spark_sql
from src.local_runner import run_local_finops_pipeline


def test_cur_generator_and_focus_schema_compliance(tmp_path: Path) -> None:
    """Verify 90-day CUR 2.0 generation and full FinOps FOCUS 1.0 column mapping."""
    raw_df = generate_cur_dataset(days=90, output_dir=tmp_path)
    assert len(raw_df) == 90 * (4 * 7 + 2)  # 4 accounts * 7 services + 2 unattached EBS volumes/day
    assert raw_df["line_item_usage_account_id"].nunique() == 4
    assert raw_df["line_item_product_code"].nunique() == 7

    focus_df = transform_cur_to_focus_df(raw_df)
    assert len(focus_df) == len(raw_df)

    for required_col in FOCUS_REQUIRED_COLUMNS:
        assert required_col in focus_df.columns, f"Missing FOCUS 1.0 column: {required_col}"

    assert set(focus_df["PricingCategory"].unique()).issubset({"Committed", "On-Demand", "Spot"})
    assert set(focus_df["TagComplianceStatus"].unique()) == {"Compliant", "Non-Compliant"}
    assert (focus_df["EffectiveCost"] > 0).all()
    assert (focus_df["ListCost"] >= focus_df["EffectiveCost"]).all()


def test_shared_cost_chargeback_allocation(tmp_path: Path) -> None:
    """Verify shared-network-transit spend is proportionally allocated across consuming business units."""
    raw_df = generate_cur_dataset(days=30, output_dir=tmp_path)
    focus_df = transform_cur_to_focus_df(raw_df)
    allocation_df = compute_shared_cost_allocation(focus_df)

    assert len(allocation_df) == 3
    total_weight = float(allocation_df["ProportionalWeightPct"].sum())
    assert 99.9 <= total_weight <= 100.1

    shared_direct_spend = float(
        focus_df.loc[focus_df["SubAccountId"] == "444455556666", "EffectiveCost"].sum()
    )
    allocated_shared_sum = float(allocation_df["AllocatedSharedSpend"].sum())
    assert abs(shared_direct_spend - allocated_shared_sum) < 1.0


def test_local_runner_and_dashboard_kpis(tmp_path: Path) -> None:
    """Verify local DuckDB lakehouse runner and Streamlit KPI computation."""
    summary = run_local_finops_pipeline(data_dir=tmp_path)
    assert summary["raw_records"] == summary["focus_records"]
    assert summary["anomalies_detected"] >= 5
    assert summary["projected_annual_savings_usd"] > 10000.0
    assert Path(summary["duckdb_path"]).exists()

    loaded = load_local_finops_data(data_dir=str(tmp_path))
    kpis = compute_executive_kpis(loaded["focus_df"], loaded["brief"])
    assert kpis["mtd_effective_spend"] > 0
    assert kpis["forecasted_monthly_spend"] >= kpis["mtd_effective_spend"]
    assert 70.0 <= kpis["tag_compliance_pct"] <= 98.0


def test_glue_spark_sql_builder() -> None:
    """Verify Glue PySpark SQL query contains all required FOCUS 1.0 projection columns."""
    sql = build_focus_spark_sql("raw_cur2_view")
    for expected_token in (
        "billing_account_id",
        "sub_account_id",
        "effective_cost",
        "tag_compliance_status",
        "allocated_cost_center",
        "billing_period",
        "linked_account_id",
    ):
        assert expected_token in sql
