import json
import os
import uuid
import time
from typing import Any, Dict, Optional
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from shared.logger import StructuredLogger
from shared.idempotency import check_or_reserve_idempotency_key, save_idempotency_result

logger = StructuredLogger("api-handler")

# Environment variables
EVALUATIONS_TABLE = os.environ.get("EVALUATIONS_TABLE", "Evaluations")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "IdempotencyKeys")
REVIEWER_GRANTS_TABLE = os.environ.get("REVIEWER_GRANTS_TABLE", "ReviewerGrants")
RUBRICS_TABLE = os.environ.get("RUBRICS_TABLE", "Rubrics")
RAW_BUCKET_NAME = os.environ.get("RAW_BUCKET_NAME", "evaluator-raw-booklets")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

s3_client = boto3.client("s3", region_name=AWS_REGION, config=Config(signature_version="s3v4"))
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Idempotency-Key",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
    }

def _extract_user_id(event: Dict[str, Any]) -> str:
    """Extract Cognito user sub from requestContext or fall back to local test identity."""
    rc = event.get("requestContext", {})
    # HTTP API JWT authorizer
    authorizer = rc.get("authorizer", {})
    jwt_claims = authorizer.get("jwt", {}).get("claims", {})
    if "sub" in jwt_claims:
        return jwt_claims["sub"]
    # REST API Cognito authorizer
    claims = authorizer.get("claims", {})
    if "sub" in claims:
        return claims["sub"]
    # Fallback / header for local development
    headers = event.get("headers") or {}
    return headers.get("x-user-id", "demo-teacher-001")

def _check_reviewer_grant(job_id: str, user_id: str) -> bool:
    """Check if requesting user has an active grant for this job."""
    try:
        table = dynamodb.Table(REVIEWER_GRANTS_TABLE)
        res = table.get_item(Key={"job_id": job_id, "reviewer_id": user_id})
        item = res.get("Item")
        if not item:
            return False
        # Verify TTL
        expires_at = item.get("expires_at", 0)
        return int(time.time()) <= int(expires_at)
    except Exception as e:
        logger.warning(f"Error checking reviewer grant: {e}")
        return False

def handle_create_job(event: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    headers = event.get("headers") or {}
    idempotency_key = headers.get("idempotency-key") or headers.get("Idempotency-Key")

    try:
        body = json.loads(event.get("body", "{}"))
    except Exception:
        return {
            "statusCode": 400,
            "headers": _cors_headers(),
            "body": json.dumps({"error": "Invalid JSON body"}),
        }

    rubric_id = body.get("rubric_id")
    if not rubric_id:
        return {
            "statusCode": 400,
            "headers": _cors_headers(),
            "body": json.dumps({"error": "Missing required field: rubric_id"}),
        }

    job_id = f"job-{uuid.uuid4()}"

    # Handle Idempotency
    if idempotency_key:
        is_new, existing = check_or_reserve_idempotency_key(
            table_name=IDEMPOTENCY_TABLE,
            idempotency_key=idempotency_key,
            job_id=job_id,
            user_id=user_id,
            ttl_hours=24,
        )
        if not is_new and existing and existing.get("response_payload"):
            logger.info(f"Returning cached idempotent response for key {idempotency_key}")
            return {
                "statusCode": 200,
                "headers": _cors_headers(),
                "body": existing["response_payload"],
            }

    s3_key = f"uploads/{user_id}/{job_id}/booklet.pdf"

    # Generate presigned POST url: 15 min TTL, 100MB limit
    try:
        presigned_post = s3_client.generate_presigned_post(
            Bucket=RAW_BUCKET_NAME,
            Key=s3_key,
            Fields={
                "acl": "private",
                "Content-Type": "application/pdf",
                "x-amz-meta-job-id": job_id,
                "x-amz-meta-user-id": user_id,
                "x-amz-meta-rubric-id": rubric_id,
            },
            Conditions=[
                {"acl": "private"},
                {"Content-Type": "application/pdf"},
                ["starts-with", "$x-amz-meta-job-id", job_id],
                ["starts-with", "$x-amz-meta-user-id", user_id],
                ["starts-with", "$x-amz-meta-rubric-id", rubric_id],
                ["content-length-range", 1024, 104857600],  # 1 KB to 100 MB
            ],
            ExpiresIn=900,
        )
    except ClientError as e:
        logger.error("Failed to generate presigned POST URL", {"error": str(e)})
        return {
            "statusCode": 500,
            "headers": _cors_headers(),
            "body": json.dumps({"error": "Failed to create upload authorization"}),
        }

    # Record initial job in Evaluations table
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    eval_table = dynamodb.Table(EVALUATIONS_TABLE)
    eval_table.put_item(
        Item={
            "job_id": job_id,
            "user_id": user_id,
            "rubric_id": rubric_id,
            "status": "PENDING_UPLOAD",
            "created_at": now_iso,
            "updated_at": now_iso,
            "s3_raw_key": s3_key,
            "metadata": body.get("metadata", {}),
        }
    )

    response_body = {
        "job_id": job_id,
        "status": "PENDING_UPLOAD",
        "upload": {
            "url": presigned_post["url"],
            "fields": presigned_post["fields"],
        },
        "expires_in_seconds": 900,
    }
    response_json = json.dumps(response_body)

    if idempotency_key:
        save_idempotency_result(IDEMPOTENCY_TABLE, idempotency_key, response_json)

    return {
        "statusCode": 201,
        "headers": _cors_headers(),
        "body": response_json,
    }

def handle_get_job(event: Dict[str, Any], user_id: str, job_id: str) -> Dict[str, Any]:
    eval_table = dynamodb.Table(EVALUATIONS_TABLE)
    try:
        res = eval_table.get_item(Key={"job_id": job_id})
        item = res.get("Item")
        if not item:
            return {
                "statusCode": 404,
                "headers": _cors_headers(),
                "body": json.dumps({"error": f"Job {job_id} not found"}),
            }

        # Authorization: owner check OR reviewer grant
        job_owner = item.get("user_id")
        if job_owner != user_id and not _check_reviewer_grant(job_id, user_id):
            return {
                "statusCode": 403,
                "headers": _cors_headers(),
                "body": json.dumps({"error": "Forbidden: Not authorized to access this evaluation"}),
            }

        return {
            "statusCode": 200,
            "headers": _cors_headers(),
            "body": json.dumps(item, default=str),
        }
    except Exception as e:
        logger.error(f"Error getting job {job_id}", {"error": str(e)})
        return {
            "statusCode": 500,
            "headers": _cors_headers(),
            "body": json.dumps({"error": "Internal server error"}),
        }

def handle_grant_reviewer(event: Dict[str, Any], user_id: str, job_id: str) -> Dict[str, Any]:
    eval_table = dynamodb.Table(EVALUATIONS_TABLE)
    try:
        res = eval_table.get_item(Key={"job_id": job_id})
        item = res.get("Item")
        if not item:
            return {"statusCode": 404, "headers": _cors_headers(), "body": json.dumps({"error": "Job not found"})}

        # Only owner can grant reviewer access
        if item.get("user_id") != user_id:
            return {"statusCode": 403, "headers": _cors_headers(), "body": json.dumps({"error": "Forbidden: Only owner can grant reviewer access"})}

        body = json.loads(event.get("body", "{}"))
        reviewer_id = body.get("reviewer_id")
        ttl_hours = body.get("ttl_hours", 72)
        if not reviewer_id:
            return {"statusCode": 400, "headers": _cors_headers(), "body": json.dumps({"error": "reviewer_id required"})}

        expires_at = int(time.time()) + (ttl_hours * 3600)
        grants_table = dynamodb.Table(REVIEWER_GRANTS_TABLE)
        grants_table.put_item(
            Item={
                "job_id": job_id,
                "reviewer_id": reviewer_id,
                "granted_by": user_id,
                "granted_at": int(time.time()),
                "expires_at": expires_at,
            }
        )

        return {
            "statusCode": 200,
            "headers": _cors_headers(),
            "body": json.dumps({"message": f"Granted reviewer {reviewer_id} access to job {job_id} for {ttl_hours}h"}),
        }
    except Exception as e:
        return {"statusCode": 500, "headers": _cors_headers(), "body": json.dumps({"error": str(e)})}

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("path") or event.get("rawPath", "")

    logger.info(f"API Request: {method} {path}")

    # Handle CORS preflight
    if method == "OPTIONS":
        return {"statusCode": 200, "headers": _cors_headers(), "body": ""}

    user_id = _extract_user_id(event)

    # Simple route matching
    if method == "POST" and (path.endswith("/api/jobs") or path.endswith("/jobs")):
        return handle_create_job(event, user_id)

    path_parts = [p for p in path.split("/") if p]
    # e.g., ["api", "jobs", "{job_id}"] or ["api", "jobs", "{job_id}", "grant"]
    if "jobs" in path_parts:
        idx = path_parts.index("jobs")
        if len(path_parts) > idx + 1:
            job_id = path_parts[idx + 1]
            if len(path_parts) > idx + 2 and path_parts[idx + 2] == "grant" and method == "POST":
                return handle_grant_reviewer(event, user_id, job_id)
            elif method == "GET":
                return handle_get_job(event, user_id, job_id)

    return {
        "statusCode": 404,
        "headers": _cors_headers(),
        "body": json.dumps({"error": f"Endpoint not found: {method} {path}"}),
    }
