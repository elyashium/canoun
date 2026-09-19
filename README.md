# Canoun — AI-Powered Examination Answer Sheet Evaluator on AWS

[![AWS](https://img.shields.io/badge/AWS-Cloud%20Native-232F3E?logo=amazon-aws)](https://aws.amazon.com/)
[![CDK v2](https://img.shields.io/badge/IaC-AWS%20CDK%20TypeScript-FF9900?logo=typescript)](https://aws.amazon.com/cdk/)
[![Amazon Bedrock](https://img.shields.io/badge/VLM%20%26%20Judge-Claude%203.5%20Haiku-8C4FFF)](https://aws.amazon.com/bedrock/)
[![Step Functions](https://img.shields.io/badge/Orchestration-Step%20Functions-FF4F8B)](https://aws.amazon.com/step-functions/)
[![DynamoDB](https://img.shields.io/badge/Storage-DynamoDB%20On--Demand-4053D6?logo=amazondynamodb)](https://aws.amazon.com/dynamodb/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An enterprise-grade, asynchronous AI evaluation pipeline designed for large-scale Indian board and university examinations (CBSE, ICSE, State Boards) 

---

## Architecture Overview

```
Teacher Browser / Client
    │
    │ 1. POST /api/jobs (Idempotency Key)
    ▼
[ Amazon API Gateway (HTTP v2) ]  ── Rate Limiting (100 rps / 200 burst) + CloudWatch Access Logs
    │
    ▼
[ ApiHandler Lambda ] ── Validates rubric, reserves DynamoDB idempotency key,
    │                    generates 15-minute presigned S3 POST URL (100MB limit)
    ▼
[ Amazon S3: raw-booklets ] ── Upload Booklet PDF (Direct teacher browser upload)
    │
    │ ObjectCreated Event
    ▼
[ Amazon SQS: trigger-queue ] ── DLQ with redrive after 3 retries
    │
    ▼
[ TriggerHandler Lambda ] ── Updates DynamoDB to PROCESSING, starts Step Functions
    │
    ▼
[ AWS Step Functions Workflow ]
    ├── 1. Splitter Lambda: PyMuPDF 200 DPI PNG split, validates %PDF- header, enforces max_pages
    │
    ├── 2. OCR Map State (Concurrency: 5):
    │      └── OcrWorker Lambda: Amazon Bedrock Claude 3.5 Haiku Multimodal Vision transcription
    │          (Exponential backoff retry on ThrottlingException)
    │
    ├── 3. Aggregator Lambda: Stitches multi-page answers, groups sections, maps rubric questions
    │
    ├── 4. Scoring Map State (Concurrency: 5):
    │      └── ScoringWorker Lambda:
    │          ├── Semantic Similarity: Cloud Map ECS Fargate Spot Sidecar (or in-process fallback)
    │          ├── Step-Marking Judge: Bedrock Claude 3.5 Haiku with failure-mode tagging
    │          └── Confidence Calibrator: Composite score (OCR clarity + similarity + judge)
    │
    ├── 5. Finalizer Lambda: Sums section totals, evaluates attempt_any questions (takes top marks),
    │      writes DONE report to DynamoDB Evaluations table, and cleans up ephemeral S3 page images
    │
    └── Catch Block ──► FailHandler Lambda: Marks job FAILED in DynamoDB with sanitized error details
```

---

## Repository Structure

```
evaluator.ai/
├── infra/                          # AWS CDK v2 TypeScript Infrastructure as Code
│   ├── bin/infra.ts                # CDK App Entrypoint supporting environment contexts
│   └── lib/
│       ├── infra-stack.ts          # EvaluatorStack wiring all constructs and outputs
│       └── constructs/
│           ├── vpc-construct.ts    # VPC, isolated subnets, 0 NAT Gateways, VPC endpoints
│           ├── storage-construct.ts# 3 S3 buckets + 4 DynamoDB tables (PAY_PER_REQUEST)
│           ├── queues-construct.ts # SQS Trigger queue with DLQ and S3 notifications
│           ├── embedding-cache-construct.ts # ECS Fargate Spot + Cloud Map Private DNS
│           ├── sagemaker-construct.ts       # SageMaker Async Inference role & endpoint config
│           ├── state-machine-construct.ts   # Step Functions pipeline with Map retry & Catch
│           ├── api-construct.ts    # HTTP API Gateway v2, Cognito User Pool, JWT authorizer
│           ├── alarms-construct.ts # 5 CloudWatch Alarms + SNS alert topic
│           ├── cost-construct.ts   # AWS Budget ($100 limit, 80% & 100% alerts)
│           └── audit-construct.ts  # AWS CloudTrail governance logging
├── lambdas/                        # Serverless Python 3.11 Worker Functions
│   ├── api_handler/                # POST /api/jobs, GET /api/jobs/{id}, POST /jobs/{id}/grant
│   ├── trigger_handler/            # SQS event consumer starting Step Functions
│   ├── splitter/                   # PyMuPDF 200 DPI renderer with page-cap enforcement
│   ├── ocr/                        # Amazon Bedrock Claude 3.5 Haiku Vision OCR worker
│   ├── aggregator/                 # Multi-page answer stitching & rubric mapping
│   ├── scorer/                     # Bedrock step-marking judge & confidence calibrator
│   ├── finalizer/                  # Section summation, attempt_any rules, cleanup
│   └── fail_handler/               # Catch handler updating job status to FAILED
├── shared/                         # Shared Utilities
│   ├── logger.py                   # Structured JSON logger with regex Indian PII masking
│   ├── idempotency.py              # DynamoDB conditional write idempotency helper
│   └── secrets.py                  # Cached Secrets Manager / environment retriever
├── embedding_cache/                # High-speed FastEmbed sidecar service
│   ├── server.py                   # FastAPI similarity & embedding microservice
│   ├── Dockerfile                  # Slim Python 3.11 with pre-baked BAAI/bge-small model
│   └── requirements.txt
├── rubrics/                        # Official Indian Examination Rubrics
│   ├── cbse-physics-class12-2025.json # CBSE Class 12 Physics (Sections A-C, step marks)
│   └── cbse-cs-class12-2025.json      # CBSE Class 12 Computer Science (Python/SQL)
├── tests/                          # Automated Pytest Unit Test Suite (10 / 10 passing)
│   ├── test_api_handler.py         # CORS, missing fields, ReviewerGrants authorization matrix
│   ├── test_splitter.py            # PDF magic header validation and error handling
│   ├── test_aggregator.py          # Multi-page continuation stitching
│   ├── test_finalizer.py           # attempt_any top-mark selection & section scoring
│   ├── test_idempotency.py         # Duplicate reservation & cached replay verification
│   └── test_logger.py              # PII redaction (Aadhaar, Roll No, Center Code)
├── RUNBOOK.md                      # Operational guide, deployment steps, & cost controls
└── walkthrough.md                  # Detailed verification & testing walkthrough
```

---

## API Specification

### 1. Create Evaluation Job
`POST /api/jobs`
- **Headers**: `Content-Type: application/json`, `Idempotency-Key: <UUID>`, `Authorization: Bearer <TOKEN>`
- **Request Body**:
  ```json
  {
    "rubric_id": "cbse-physics-class12-2025",
    "metadata": {
      "student_code": "CANDIDATE-491"
    }
  }
  ```
- **Response (201 Created)**:
  ```json
  {
    "job_id": "job-a3b4c5d6-...",
    "status": "PENDING_UPLOAD",
    "upload": {
      "url": "https://evaluator-raw-booklets.s3.ap-south-1.amazonaws.com/",
      "fields": {
        "key": "uploads/user-123/job-a3b4c5d6/booklet.pdf",
        "x-amz-meta-job-id": "job-a3b4c5d6",
        "policy": "...",
        "x-amz-signature": "..."
      }
    },
    "expires_in_seconds": 900
  }
  ```

### 2. Fetch Evaluation Report
`GET /api/jobs/{job_id}`
- **Headers**: `Authorization: Bearer <TOKEN>`
- **Authorization Guard**: Validates that requesting user is the job owner **or** holds an active `ReviewerGrant` (validated at read-time).
- **Response (200 OK)**:
  ```json
  {
    "job_id": "job-a3b4c5d6",
    "status": "DONE",
    "total_marks_awarded": 18.5,
    "max_total_marks": 21.0,
    "percentage": 88.1,
    "section_breakdown": {
      "Section A": { "marks_awarded": 5.0, "max_marks": 5.0 },
      "Section B": { "marks_awarded": 4.5, "max_marks": 6.0 },
      "Section C": { "marks_awarded": 9.0, "max_marks": 10.0 }
    },
    "failure_mode_summary": {
      "MISSING_UNITS": 1
    },
    "questions": [ ... ]
  }
  ```

### 3. Grant Reviewer Access (IRR Moderation)
`POST /api/jobs/{job_id}/grant`
- **Headers**: `Authorization: Bearer <TOKEN>` (Must be job owner)
- **Request Body**:
  ```json
  {
    "reviewer_id": "moderator@board.edu.in",
    "ttl_hours": 48
  }
  ```

---

## Automated Test Suite

The repository includes comprehensive unit testing covering all security, data governance, and parsing edge cases:

```bash
# Run test suite
py -3.11 -m pytest tests/ -v
```

```
============================= test session starts =============================
tests/test_aggregator.py::test_aggregator_multi_page_stitching PASSED    [ 10%]
tests/test_api_handler.py::test_api_handler_cors_options PASSED          [ 20%]
tests/test_api_handler.py::test_api_handler_create_job_missing_rubric PASSED [ 30%]
tests/test_api_handler.py::test_reviewer_grants_authorization PASSED     [ 40%]
tests/test_finalizer.py::test_attempt_any_logic PASSED                   [ 50%]
tests/test_idempotency.py::test_idempotency_flow PASSED                  [ 60%]
tests/test_logger.py::test_mask_pii_patterns PASSED                      [ 70%]
tests/test_logger.py::test_sanitize_dict_keys PASSED                     [ 80%]
tests/test_splitter.py::test_invalid_pdf_magic_header PASSED             [ 90%]
tests/test_splitter.py::test_valid_pdf_magic_header PASSED               [100%]
============================= 10 passed in 14.82s =============================
```

---

## Deployment & Operation

### 1. Prerequisites
- AWS CLI configured (`aws configure`, region `ap-south-1` recommended).
- Node.js 20+ and Python 3.11 installed.
- Amazon Bedrock model access enabled for **Anthropic Claude 3.5 Haiku**.

### 2. Deploy with AWS CDK
```bash
cd infra

# Build TypeScript
npm run build

# Bootstrap environment (one-time per account/region)
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-south-1

# Deploy stack
npx cdk deploy EvaluatorStack-dev -c environment=dev --require-approval never
```

### 3. Standby Mode (Pause Fargate Billing)
Between testing sessions, pause Fargate Spot billing by scaling tasks to 0:
```bash
CLUSTER=$(aws ecs list-clusters --query "clusterArns[?contains(@, 'EvaluatorCluster')]" --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --query "serviceArns[0]" --output text)

aws ecs update-service --cluster $CLUSTER --service $SERVICE --desired-count 0
```
*(The evaluation pipeline automatically falls back to in-process similarity computation when the sidecar is paused).*

### 4. Complete Tear-Down
```bash
cd infra
npx cdk destroy EvaluatorStack-dev -c environment=dev --force
```

Refer to [`RUNBOOK.md`](RUNBOOK.md) for full operational guides and verification checklists.

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
