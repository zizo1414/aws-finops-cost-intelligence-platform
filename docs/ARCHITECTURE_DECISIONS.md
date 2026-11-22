# Architecture Decision Records (ADRs)

This document captures the principal Solutions Architecture trade-offs and design decisions behind the **`aws-finops-cost-intelligence-platform`**.

---

## ADR-001: Normalize AWS CUR 2.0 into the FinOps FOCUS 1.0 Open Standard

### Context
Raw AWS Cost and Usage Reports (CUR 2.0) contain over 120 provider-specific columns (`line_item_unblended_cost`, `reservation_effective_cost`, `savings_plan_savings_plan_effective_cost`, `pricing_public_on_demand_cost`) where computing accurate amortized spend requires complex conditional SQL logic across Reserved Instances, Savings Plans, and Spot usage.

### Decision
Normalize all ingested CUR 2.0 exports into the **FinOps Open Cost & Usage Specification (FOCUS 1.0)** schema via an AWS Glue 4.0 PySpark ETL job (and DuckDB locally) before exposing tables to downstream analytics, anomaly detection, or BI dashboards.

### Consequences
- **Pros**:
  - Standardizes cost metrics into three unambiguous columns: `BilledCost` (cash), `EffectiveCost` (amortized net spend), and `ListCost` (public On-Demand baseline).
  - Simplifies SQL queries for finance and engineering teams by 70%.
  - Future-proofs the data lakehouse to ingest Azure, GCP, Databricks, or Snowflake billing feeds into the exact same table schema.
- **Cons**:
  - Introduces a lightweight daily ETL step (~2 minutes on 2 Glue `G.1X` workers).

---

## ADR-002: S3 + Glue + Athena Lakehouse vs. Relying Solely on AWS Cost Explorer

### Context
AWS Cost Explorer provides out-of-the-box visualizations, but Enterprise FinOps teams frequently hit limitations around resource-level granularity, custom chargeback allocation rules, long-term retention, and programmatic join capabilities.

### Decision
Build a serverless **S3 + Glue Data Catalog + Amazon Athena** FinOps Lakehouse (paired with DuckDB/Streamlit and BI tools like QuickSight or Power BI) rather than relying solely on AWS Cost Explorer.

### Consequences
- **Pros**:
  - **Unlimited Resource-Level Retention**: Cost Explorer resource-level hourly/daily data is time-limited; S3 Parquet retains multi-year resource-level history for pennies per GB.
  - **Zero API Cost for Custom Analytics**: Cost Explorer API charges $0.01 per paginated request, which becomes expensive for automated daily tooling; Athena queries over partitioned Parquet cost fractions of a cent per query.
  - **Custom Showback/Chargeback Joins**: Enables joining billing data with CMDB metadata, HR cost-center hierarchies, and proportional shared-service allocation rules in SQL.
- **Cons**:
  - Requires managing S3 buckets, Glue catalog metadata, and Athena workgroup guardrails (automated via CDK in `FinOpsDataLakeStack`).

---

## ADR-003: Deterministic Rolling-Window Z-Score + Amazon Bedrock vs. Black-Box ML Anomaly Detection

### Context
Many commercial FinOps tools use opaque machine-learning models to flag cost anomalies. When an alert fires, engineers often distrust it because the threshold logic is hidden and the alert lacks concrete AWS architectural remediation steps.

### Decision
Decouple **anomaly detection** from **executive explanation**:
1. Use a **deterministic 14-day rolling statistical baseline** (`z_score >= 2.2`, `pct_spike >= 30%`, `dollar_delta >= $25/day`) per `(SubAccountId, ServiceName)` in SQL to detect anomalies transparently.
2. Pass the verified anomaly payload and top resource drivers to **Amazon Bedrock (`Claude 3 Haiku` / `Amazon Nova Micro`)** to generate plain-English executive summaries and service-specific remediation steps (e.g., S3 Gateway VPC Endpoints for NAT Gateway spikes, Graviton rightsizing for RDS).

### Consequences
- **Pros**:
  - **100% Explainable & Auditable Math**: Every flagged spike displays its exact trailing 14-day mean, standard deviation, dollar delta, and z-score.
  - **Actionable Engineering Context**: Amazon Bedrock translates raw billing line items into concrete architectural fixes without hallucinating numbers (all dollar calculations are computed deterministically prior to prompt construction).
  - **Near-Zero Inference Cost**: Calling Bedrock once daily on summarized JSON payloads (<2 KB) costs **<$0.05/month**.
- **Cons**:
  - Requires at least 14 days of historical baseline data per account/service before z-score alerts activate.

---

## ADR-004: Multi-Account Tag Governance & Three-Tier Showback/Chargeback Enforcement

### Context
In multi-account AWS Organizations, 100% tag compliance is rarely achieved through policy alone. Untagged resources ("shadow IT" or ad-hoc sandbox experiments) often fall into an unallocated bucket that finance teams cannot charge back.

### Decision
Enforce a **Three-Tier Cost Allocation Model** inside the FOCUS transformation layer (`src/focus_transformer.py`):
1. **Tier 1 — Direct Tag Chargeback**: When mandatory tags (`CostCenter`, `Environment`, `Owner`, `Project`) are present, `TagComplianceStatus = 'Compliant'` and spend is charged directly to `resource_tags_user_cost_center`.
2. **Tier 2 — Account-Level Fallback Showback**: When a resource is missing any mandatory tag, `TagComplianceStatus = 'Non-Compliant'`, the resource is surfaced on the Tag Governance leaderboard, and its cost automatically falls back to the owning workload account's default cost center (`CC-2010-CORE-ENG (Account-Fallback)`).
3. **Tier 3 — Proportional Shared Infrastructure Allocation**: Spend in shared platform accounts (`shared-network-transit` / `444455556666`) is distributed proportionally across business units based on their share of direct effective cloud spend.

### Consequences
- **Pros**:
  - Guarantees **100% cost allocation** for Finance on day one while giving Engineering transparent visibility into tag non-compliance by account and resource ARN.
- **Cons**:
  - Account-level fallback requires maintaining an account-to-cost-center mapping table in the transformation layer.
