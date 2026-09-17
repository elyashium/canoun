# Evaluator.ai — AWS Production Runbook & Cost-Control Guide

This guide provides step-by-step instructions for deploying, managing costs, testing, and tearing down **evaluator.ai** on AWS within a $100 credit budget for the WeMakeDevs × AWS "First Commit" Hackathon.

---

## 1. Cost Architecture & Budget Safeguards

- **Zero NAT Gateway Idle Costs**: All Lambdas and Fargate tasks run inside a VPC with free S3 & DynamoDB Gateway Endpoints and Interface VPC Endpoints.
- **No ALB Hourly Fees**: The embedding cache sidecar uses **AWS Cloud Map Service Discovery (Private DNS)** (`embedding-cache.evaluator.local:8000`), completely eliminating the ~$16/mo Application Load Balancer baseline charge.
- **Zero Idle ECR Failures**: The VPC includes both `ecr.api` and `ecr.dkr` interface endpoints so Fargate Spot tasks pull images smoothly in isolated subnets without a NAT Gateway.
- **Unused SageMaker Endpoint Removed**: Avoids ~$0.02/hr idle endpoint charges; scoring uses Bedrock + embedding similarity.
- **DynamoDB On-Demand (PAY_PER_REQUEST)**: All 4 tables (`Evaluations`, `IdempotencyKeys`, `Rubrics`, `ReviewerGrants`) bill strictly per read/write with 0 provisioned capacity idle charges.
- **Bedrock Throttling Protection**: Step Functions tasks feature exponential backoff retries (`ThrottlingException`, `Lambda.TooManyRequestsException`, jitter: FULL) across concurrent Map states.
- **HTTP API Abuse Protection**: Default rate limiting (`rateLimit: 20 req/s`, `burstLimit: 50`) prevents runaway Bedrock invocation costs.
- **CloudWatch Access Logs**: Full JSON-structured access logging on API Gateway for live debugging.
- **Budget Guardrail**: CloudWatch Budget strictly enforces a $100 limit with 80% ($80) and 100% ($100) SNS alerts.

---

## 2. Prerequisites

1. **AWS Account & CLI**:
   - Configure AWS CLI with credentials:
     ```bash
     aws configure
     # AWS Access Key ID: <YOUR_KEY>
     # AWS Secret Access Key: <YOUR_SECRET>
     # Default region name: ap-south-1
     # Default output format: json
     ```
   - Verify identity:
     ```bash
     aws sts get-caller-identity
     ```
2. **Bedrock Model Access**:
   - In AWS Console -> Amazon Bedrock -> Model Access -> Enable **Anthropic Claude 3.5 Haiku**.

---

## 3. Deployment Steps (Once AWS Credits Arrive)

### Step 1: Bootstrap CDK (One-time per AWS account/region)
```bash
cd infra
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-south-1
```

### Step 2: Deploy Stack
```bash
# Preview changes
npx cdk diff -c environment=dev

# Deploy the complete stack
npx cdk deploy EvaluatorStack-dev -c environment=dev --require-approval never
```

CDK Outputs after deployment:
- `ApiUrl`: The public HTTPS endpoint for the evaluator API.
- `UserPoolId`: Cognito User Pool for teacher authentication.
- `UserPoolClientId`: Cognito App Client ID.
- `RawBookletsBucketName`: Private S3 bucket for uploads.
- `StateMachineArn`: Step Functions State Machine ARN.
- `EmbeddingCacheServiceUrl`: `http://embedding-cache.evaluator.local:8000` (Cloud Map private DNS).

---

## 4. End-to-End Evaluation Workflow

### Step 1: Create a Teacher User in Cognito
```bash
aws cognito-idp sign-up \
  --client-id <UserPoolClientId> \
  --username teacher@example.com \
  --password "TeacherPass123!" \
  --user-attributes Name=email,Value=teacher@example.com
```

### Step 2: Request Upload Authorization (`POST /api/jobs`)
```bash
curl -X POST "<ApiUrl>/api/jobs" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: test-req-001" \
  -H "x-user-id: teacher@example.com" \
  -d '{
    "rubric_id": "cbse-physics-class12-2025",
    "metadata": { "student_code": "STU-882" }
  }'
```
Response returns:
```json
{
  "job_id": "job-xxxxxxxx",
  "status": "PENDING_UPLOAD",
  "upload": {
    "url": "https://evaluator-raw-booklets.s3.ap-south-1.amazonaws.com/",
    "fields": { ... }
  },
  "expires_in_seconds": 900
}
```

### Step 3: Upload Answer Sheet PDF via Presigned S3 POST
```bash
curl -X POST "<upload.url>" \
  -F "key=<upload.fields.key>" \
  -F "x-amz-meta-job-id=<job_id>" \
  -F "x-amz-meta-user-id=teacher@example.com" \
  -F "x-amz-meta-rubric-id=cbse-physics-class12-2025" \
  -F "file=@sample_booklet.pdf"
```

### Step 4: Pipeline Execution (Automatic)
1. S3 emits `ObjectCreated` event to `evaluator-trigger-queue` (SQS).
2. `trigger-handler` Lambda receives event and starts Step Functions execution.
3. `splitter` Lambda validates PDF magic header `%PDF-`, enforces rubric `max_pages` (default 32 for physics), renders 200 DPI PNGs to ephemeral S3.
4. `ocr-worker` Lambda runs concurrently across pages via Bedrock Claude 3.5 Haiku vision (with exponential backoff retries).
5. `aggregator` Lambda stitches multi-page answers and matches rubric questions.
6. `scoring-worker` Lambda checks Cloud Map embedding cache sidecar (or in-process fastembed fallback), evaluates step-marking via Bedrock LLM-as-a-judge, and calculates calibrated confidence.
7. `finalizer` Lambda sums section marks, selects best answers for `attempt_any` questions, writes `DONE` status to DynamoDB, and deletes ephemeral page images.

### Step 5: Poll Evaluation Report (`GET /api/jobs/{job_id}`)
```bash
curl -X GET "<ApiUrl>/api/jobs/<job_id>" \
  -H "x-user-id: teacher@example.com"
```

### Step 6: Reviewer Collaboration (`POST /api/jobs/{job_id}/grant`)
Owner delegates access to a second examiner:
```bash
curl -X POST "<ApiUrl>/api/jobs/<job_id>/grant" \
  -H "Content-Type: application/json" \
  -H "x-user-id: teacher@example.com" \
  -d '{
    "reviewer_id": "reviewer@example.com",
    "ttl_hours": 48
  }'
```
Now `reviewer@example.com` can fetch the evaluation report with their own credentials.

---

## 5. Cost Control & Standby Operations

### Pause Work / Overnight Standby (Preserve Stack, Stop Fargate Billing)
To pause testing without destroying the stack, scale the Fargate Spot service to 0 tasks:
```bash
CLUSTER_NAME=$(aws ecs list-clusters --query "clusterArns[?contains(@, 'EvaluatorCluster')]" --output text)
SERVICE_NAME=$(aws ecs list-services --cluster $CLUSTER_NAME --query "serviceArns[0]" --output text)

aws ecs update-service --cluster $CLUSTER_NAME --service $SERVICE_NAME --desired-count 0
```
*(The scoring worker Lambda automatically uses its in-process fastembed/token similarity fallback when the sidecar is stopped!)*

When ready to resume testing with the sidecar:
```bash
aws ecs update-service --cluster $CLUSTER_NAME --service $SERVICE_NAME --desired-count 1
```

### Complete Tear-Down (Eliminate ALL Recurring Charges)
When finished testing for the day or after the hackathon judging, destroy the stack to stop all interface endpoint billing (~$0.07/hr):
```bash
cd infra
npx cdk destroy EvaluatorStack-dev -c environment=dev --force
```

---

## 6. Automated Verification Tests

Run the full local test suite:
```bash
py -3.11 -m pytest tests/ -v
```

All 10 test scenarios pass:
1. `test_api_handler_cors_options`: Verifies CORS headers on preflight requests.
2. `test_api_handler_create_job_missing_rubric`: Validates 400 Bad Request on missing rubric.
3. `test_reviewer_grants_authorization`: Validates owner read (200), stranger read (403), unauthorized delegation (403), owner grant creation (200), granted reviewer read (200), and expired grant rejection (403).
4. `test_attempt_any_logic`: Verifies CBSE Section C choice selection (best 2 out of 3 counted).
5. `test_idempotency_flow`: Verifies duplicate request key conditional reservation and cached response replay.
6. `test_mask_pii_patterns`: Verifies Aadhaar, CBSE roll numbers, phone numbers, and emails are redacted in logs.
7. `test_sanitize_dict_keys`: Verifies sensitive dict keys (student name, roll, secret) are masked.
8. `test_invalid_pdf_magic_header`: Rejects corrupted/spoofed PDF files.
9. `test_valid_pdf_magic_header`: Accepts valid `%PDF-` document format.
10. `test_aggregator_multi_page_stitching`: Stitches multi-page answers across pages 1 and 2 into unified question response.
