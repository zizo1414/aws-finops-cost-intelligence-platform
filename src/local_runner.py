#!/usr/bin/env python3
"""Local End-to-End FinOps FOCUS Lakehouse Runner (DuckDB + Anomaly Detector + Bedrock Advisor)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.generate_cur_data import generate_cur_dataset
from src.anomaly_detector import detect_spend_anomalies, summarize_idle_and_untagged_waste
from src.bedrock_advisor import invoke_bedrock_finops_advisor
from src.focus_transformer import (
    compute_shared_cost_allocation,
    transform_cur_to_focus_df,
    write_focus_curated_parquet,
)


def run_local_finops_pipeline(data_dir: str | Path | None = None) -> dict:
    """Execute the full CUR 2.0 -> FOCUS 1.0 -> Anomaly Detection -> Bedrock Brief pipeline offline."""
    base_dir = Path(data_dir or os.environ.get("LOCAL_DATA_DIR", "data"))
    raw_parquet = base_dir / "raw_cur2" / "cur2_90d_export.parquet"

    if not raw_parquet.exists():
        raw_df = generate_cur_dataset(output_dir=base_dir)
    else:
        raw_df = pd.read_parquet(raw_parquet)

    # 1. Transform raw CUR 2.0 into FinOps FOCUS 1.0
    focus_df = transform_cur_to_focus_df(raw_df)
    curated_parquet = write_focus_curated_parquet(focus_df, base_dir)

    # 2. Compute showback/chargeback shared cost allocation
    chargeback_df = compute_shared_cost_allocation(focus_df)

    # 3. Detect deterministic rolling 14-day z-score and % spend spikes
    anomalies_df = detect_spend_anomalies(focus_df)

    # 4. Catalog idle and untagged resource waste
    waste_df = summarize_idle_and_untagged_waste(focus_df)

    # 5. Generate Amazon Bedrock FinOps Executive Optimization Brief (offline simulation)
    advisor_brief = invoke_bedrock_finops_advisor(
        anomalies_df=anomalies_df,
        waste_df=waste_df,
        demo_mode=True,
    )

    # 6. Persist all tables into local DuckDB lakehouse for the Streamlit Console
    duckdb_path = base_dir / "finops_lakehouse.duckdb"
    if duckdb_path.exists():
        duckdb_path.unlink()

    con = duckdb.connect(str(duckdb_path))
    con.register("raw_df", raw_df)
    con.register("focus_df", focus_df)
    con.register("chargeback_df", chargeback_df)
    con.register("anomalies_df", anomalies_df)
    con.register("waste_df", waste_df)

    con.execute("CREATE TABLE raw_cur2 AS SELECT * FROM raw_df")
    con.execute("CREATE TABLE focus_cost_and_usage AS SELECT * FROM focus_df")
    con.execute("CREATE TABLE chargeback_allocation AS SELECT * FROM chargeback_df")
    con.execute("CREATE TABLE spend_anomalies AS SELECT * FROM anomalies_df")
    con.execute("CREATE TABLE idle_and_untagged_waste AS SELECT * FROM waste_df")
    con.close()

    brief_json_path = base_dir / "bedrock_executive_brief.json"
    brief_json_path.write_text(json.dumps(advisor_brief, indent=2), encoding="utf-8")

    total_effective_spend = float(focus_df["EffectiveCost"].sum())
    compliant_spend = float(
        focus_df.loc[focus_df["TagComplianceStatus"] == "Compliant", "EffectiveCost"].sum()
    )
    tag_compliance_pct = round((compliant_spend / max(total_effective_spend, 1.0)) * 100.0, 2)

    summary = {
        "raw_records": len(raw_df),
        "focus_records": len(focus_df),
        "curated_parquet": str(curated_parquet),
        "duckdb_path": str(duckdb_path),
        "total_90d_effective_spend_usd": round(total_effective_spend, 2),
        "tag_compliance_pct": tag_compliance_pct,
        "anomalies_detected": len(anomalies_df),
        "projected_annual_savings_usd": advisor_brief["projected_annual_savings_usd"],
    }
    return summary


if __name__ == "__main__":
    metrics = run_local_finops_pipeline()
    print("=" * 78)
    print("AWS FINOPS & COST INTELLIGENCE LAKEHOUSE (LOCAL FOCUS 1.0 PIPELINE SUMMARY)")
    print("=" * 78)
    for k, v in metrics.items():
        print(f"  {k:<34}: {v}")
    print("=" * 78)
