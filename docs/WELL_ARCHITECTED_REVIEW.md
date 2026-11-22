# AWS Well-Architected Framework Review: Cloud FinOps & Cost Intelligence Platform

This document evaluates the **`aws-finops-cost-intelligence-platform`** across all six pillars of the **AWS Well-Architected Framework**, with an in-depth architectural assessment of the **Cost Optimization** pillar.

---

## 1. Cost Optimization Pillar (Primary Focus)

### Practice Cloud Financial Management (COST01 / COST02)
- **Standardized Multi-Cloud / Multi-Account Taxonomy (FinOps FOCUS 1.0)**: Raw AWS Data Exports (CUR 2.0) are normalized into the open **FOCUS 1.0** schema (`BilledCost`, `EffectiveCost`, `ListCost`, `PricingCategory`, `ServiceCategory`), enabling finance, engineering, and executive stakeholders to speak a single financial language.
- **Automated Showback & Proportional Chargeback**: Workload accounts map directly to business unit cost centers (`CC-2010-CORE-ENG`, `CC-3020-DATA-LAKE`, `CC-4050-GENAI-LABS`), while shared transit/networking infrastructure (`444455556666`) is allocated proportionally based on each business unit's direct effective spend.

### Expenditure and Usage Awareness (COST03 / COST04)
- **Automated Tag Hygiene Governance**: Every CUR 2.0 line item is evaluated against mandatory cost-allocation tags (`CostCenter`, `Environment`, `Owner`, `Project`). Non-compliant resources are flagged immediately and attributed via account-level fallback rules so zero spend remains unallocated.
- **Near-Real-Time Anomaly Detection**: Rather than waiting for month-end billing surprises, an EventBridge-triggered daily pipeline computes rolling 14-day z-scores and percentage spikes per `(SubAccountId, ServiceName)` and dispatches executive remediation briefs via Amazon Bedrock and Amazon SNS.

### Cost-Effective Resources & Pricing Models (COST05 – COST09)
- **Commitment Discount Tracking (`EffectiveCost` vs `ListCost`)**: Tracks Savings Plans and Reserved Instance coverage alongside uncovered On-Demand spend, quantifying Effective Savings Rate (ESR) and identifying high-ROI commitment opportunities (such as converting steady-state `db.r6i` RDS clusters to Graviton `db.r6g` Reserved Instances).
- **Network & Storage Waste Elimination**: Specifically detects high-cost anti-patterns including runaway NAT Gateway data processing charges (remediated with free S3 Gateway VPC Endpoints) and unattached `gp3` EBS volumes.
- **Meta-FinOps Guardrails on the Platform Itself**:
  - **Athena Scan Limits**: Enforces a hard `10 GB` `BytesScannedCutoffPerQuery` on the `finops_cost_intelligence_wg` workgroup.
  - **S3 Lifecycle Tiering**: Raw CUR 2.0 exports transition to `INTELLIGENT_TIERING` after 30 days and `GLACIER` after 180 days; Athena scratch query results auto-expire after 14 days.
  - **Serverless Scale-to-Zero**: Glue jobs, Lambda functions, Step Functions, and DynamoDB (`PAY_PER_REQUEST`) incur zero idle compute charges.

---

## 2. Operational Excellence Pillar

- **100% Infrastructure as Code (AWS CDK — Python)**: All storage, catalogs, ETL jobs, state machines, IAM roles, and alerting topics are defined declaratively across three modular stacks (`FinOpsDataLakeStack`, `TransformationAndAnomalyStack`, `AdvisorAndAlertingStack`).
- **Offline Determinism & CI/CD Verification**: Engineers can validate infrastructure synthesis (`cdk synth`), unit assertions (`aws_cdk.assertions`), FOCUS transformations, and anomaly detection locally via DuckDB (`DEMO_MODE=true`) and in GitHub Actions without requiring live AWS credentials.
- **Zero-Touch Environment Bootstrapping**: `scripts/export_env.py` inspects CloudFormation stack outputs post-deployment to populate `.env` automatically.

---

## 3. Security Pillar

- **Encryption Everywhere with Customer-Managed KMS**: A single customer-managed KMS key (`alias/finops-cost-intelligence-lakehouse`) with automatic annual key rotation encrypts Raw CUR 2.0 S3 objects, Curated FOCUS Parquet tables, Athena query results, DynamoDB anomaly records, and SNS notifications.
- **Strict S3 Bucket Posture**: All buckets enforce `BlockPublicAccess.BLOCK_ALL`, `enforce_ssl=True` (`aws:SecureTransport`), and S3 Bucket Keys to reduce KMS request costs by up to 99%.
- **Least-Privilege IAM Policies**:
  - Glue ETL role is scoped strictly to the raw and curated S3 bucket ARNs and the `finops_focus_curated` Glue catalog database.
  - Bedrock Advisor Lambda is restricted to `bedrock:InvokeModel` on approved foundation model ARNs (`anthropic.claude-3-haiku-20240307-v1:0` and `amazon.nova-micro-v1:0`).

---

## 4. Reliability Pillar

- **Managed Serverless Orchestration**: AWS Step Functions coordinates the daily Glue ETL job and Anomaly Detection Lambda with built-in execution tracking and X-Ray distributed tracing enabled.
- **Data Durability & Recovery**: S3 buckets enable object versioning; DynamoDB `FinOpsAnomaliesTable` enables Point-in-Time Recovery (PITR) for continuous 35-day backup protection and automatic TTL cleanup of expired findings.

---

## 5. Performance Efficiency Pillar

- **Columnar Parquet & Partition Pruning**: Curated FOCUS tables are stored in Snappy-compressed Apache Parquet partitioned by `billing_period` (`YYYY-MM`) and `linked_account_id`, reducing Athena scan volumes by >90% compared to unpartitioned CSV billing exports.
- **Low-Latency In-Memory Analytics**: Local and edge analytics leverage **DuckDB** vectorized execution over Parquet files for sub-second interactive dashboard filtering.

---

## 6. Sustainability Pillar

- **Elimination of Idle Cloud Compute & Storage**: Identifying and terminating unattached EBS volumes, oversized low-utilization RDS instances, and runaway recursive GenAI agent loops directly reduces unused data center energy consumption.
- **Serverless Event-Driven Execution**: The FinOps platform runs once daily for <3 minutes rather than maintaining 24/7 provisioned BI servers or database clusters.
