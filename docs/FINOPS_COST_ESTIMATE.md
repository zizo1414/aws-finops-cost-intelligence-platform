# Meta-FinOps Cost Estimate: Running the Platform for <$25/Month

A core principle of Cloud FinOps is that **your cost intelligence tooling should never become a cost problem itself**. This document provides a transparent, bottoms-up monthly cost estimate for running the **`aws-finops-cost-intelligence-platform`** in production across a 10-to-50 AWS account organization (~15 GB of compressed CUR 2.0 Parquet data per month).

---

## Monthly AWS Infrastructure Cost Breakdown (`us-east-1`)

| AWS Service | Usage Assumption (Monthly) | Unit Pricing (`us-east-1`) | Estimated Monthly Cost (USD) |
| :--- | :--- | :--- | ---: |
| **Amazon S3 (Raw CUR 2.0 + Curated FOCUS Parquet)** | 30 GB Standard / Intelligent-Tiering storage + 50,000 PUT/GET requests (S3 Bucket Key enabled) | $0.023 / GB-month | **$0.75** |
| **AWS KMS (Customer-Managed Key)** | 1 Customer-Managed Symmetric Key + <50,000 API calls (99% reduced via S3 Bucket Keys) | $1.00 / key / month + $0.03 / 10k requests | **$1.15** |
| **AWS Glue Data Catalog** | 2 Databases (`finops_raw`, `finops_focus_curated`) + <1,000 partitions (First 1M objects free) | $0.00 (Free Tier) | **$0.00** |
| **AWS Glue 4.0 PySpark ETL Job** | Runs once daily for 2.5 minutes using 2 `G.1X` workers (2.5 DPU-hours/month total) | $0.44 / DPU-hour | **$1.10** |
| **Amazon Athena (`finops_cost_intelligence_wg`)** | Daily anomaly query + 150 dashboard queries scanning ~150 MB partitioned Parquet each (~25 GB/month scanned) | $5.00 / TB scanned | **$0.13** |
| **AWS Step Functions + Amazon EventBridge** | 30 daily state machine executions (3 state transitions each = 90 transitions/month) | First 4,000 state transitions free | **$0.00** |
| **AWS Lambda (Anomaly Detector + Bedrock Advisor)** | 60 invocations/month @ 512 MB RAM, 15s average duration | Within AWS Lambda Perpetual Free Tier | **$0.00** |
| **Amazon DynamoDB (`FinOpsAnomaliesTable`)** | `PAY_PER_REQUEST` On-Demand: <50,000 writes & reads/month + <50 MB storage with PITR enabled | $1.25 / million WRUs + $0.25 / million RRUs | **$0.25** |
| **Amazon Bedrock (`Claude 3 Haiku` / `Nova Micro`)** | 30 daily executive brief generations (~2,500 input tokens + 900 output tokens per run) | $0.25 / 1M input tokens, $1.25 / 1M output tokens | **$0.06** |
| **Amazon SNS + CloudWatch Logs** | 30 daily email/Slack notifications + 1 GB log ingestion | Within SNS Free Tier + $0.50 / GB logs | **$0.50** |
| **Total Monthly Platform Operating Cost** | **100% Serverless & Event-Driven** | | **$3.94 / month** *(well under the <$25/mo budget)* |

---

## FinOps Return on Investment (ROI) Analysis

Even for a mid-sized enterprise spending **$50,000/month ($600,000/year)** on AWS, this platform typically uncovers **15% to 30% in actionable cloud waste** within the first 30 days:

| Optimization Lever | Typical Monthly Waste Uncovered | Remediation Mechanism |
| :--- | ---: | :--- |
| **NAT Gateway Data Processing Offload** | $1,200 – $3,500 / mo | Identifying high-volume S3/DynamoDB/ECR traffic traversing NAT Gateways and routing via free VPC Gateway Endpoints |
| **Database & Compute Rightsizing + Graviton** | $2,500 – $6,000 / mo | Downsizing <15% CPU RDS/EC2 instances to Graviton3 (`r6g`/`m7g`) and applying 1-Year No-Upfront Savings Plans |
| **Unattached EBS & Orphaned Snapshot Cleanup** | $600 – $1,800 / mo | Automated lifecycle snapshot + deletion of unattached `gp3`/`io2` volumes sitting in `available` state >30 days |
| **Shadow GenAI & Athena Scan Guardrails** | $800 – $2,200 / mo | Terminating untagged recursive Bedrock agent loops, routing routine prompts to Haiku/Nova, and enforcing 10 GB Athena query cutoffs |
| **Total Typical Savings Realized** | **$5,100 – $13,500 / month** | **>1,200x ROI vs. the ~$4/month platform operating cost** |
