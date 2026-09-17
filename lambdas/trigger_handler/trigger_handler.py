import json
import os
import urllib.parse
from typing import Any, Dict
import boto3
from shared.logger import StructuredLogger

logger = StructuredLogger("trigger-handler")

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
EVALUATIONS_TABLE = os.environ.get("EVALUATIONS_TABLE", "Evaluations")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

s3_client = boto3.client("s3", region_name=AWS_REGION)
sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process SQS messages containing S3 ObjectCreated events,
    update job status, and start Step Functions workflow.
    """
    records = event.get("Records", [])
    logger.info(f"Processing {len(records)} trigger records")

    for record in records:
        body = json.loads(record.get("body", "{}"))
        # S3 event may be wrapped directly or in an SNS/EventBridge message
        s3_records = body.get("Records", [])
        if not s3_records and "detail" in body:
            # EventBridge format
            detail = body["detail"]
            bucket_name = detail.get("bucket", {}).get("name")
            s3_key = detail.get("object", {}).get("key")
        elif s3_records:
            # S3 event notification format
            s3_info = s3_records[0].get("s3", {})
            bucket_name = s3_info.get("bucket", {}).get("name")
            raw_key = s3_info.get("object", {}).get("key", "")
            s3_key = urllib.parse.unquote_plus(raw_key)
        else:
            logger.warning("Unrecognized message body format", {"body": body})
            continue

        if not bucket_name or not s3_key:
            continue

        logger.info(f"S3 Object detected: s3://{bucket_name}/{s3_key}")

        # Fetch S3 metadata
        try:
            head = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            metadata = head.get("Metadata", {})
        except Exception as e:
            logger.error(f"Failed to head object s3://{bucket_name}/{s3_key}", {"error": str(e)})
            continue

        job_id = metadata.get("job-id")
        user_id = metadata.get("user-id")
        rubric_id = metadata.get("rubric-id")

        # Fallback to parsing key if metadata not set: uploads/{user_id}/{job_id}/booklet.pdf
        if not job_id and "uploads/" in s3_key:
            parts = s3_key.split("/")
            if len(parts) >= 4:
                user_id = user_id or parts[1]
                job_id = job_id or parts[2]

        if not job_id:
            logger.error(f"Cannot identify job_id for s3://{bucket_name}/{s3_key}")
            continue

        logger.set_job_id(job_id)

        # Update DynamoDB status to PROCESSING
        try:
            eval_table = dynamodb.Table(EVALUATIONS_TABLE)
            eval_table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #status = :status, #proc_start = :proc_start",
                ExpressionAttributeNames={
                    "#status": "status",
                    "#proc_start": "processing_started_at",
                },
                ExpressionAttributeValues={
                    ":status": "PROCESSING",
                    ":proc_start": int(context.get_remaining_time_in_millis() if hasattr(context, "get_remaining_time_in_millis") else 0),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to update job {job_id} status in DynamoDB: {e}")

        # Start Step Functions execution
        if STATE_MACHINE_ARN:
            sfn_input = {
                "job_id": job_id,
                "user_id": user_id,
                "rubric_id": rubric_id or "cbse-physics-class12-2025",
                "raw_bucket": bucket_name,
                "raw_key": s3_key,
            }
            try:
                execution_name = f"{job_id}"
                sfn_res = sfn_client.start_execution(
                    stateMachineArn=STATE_MACHINE_ARN,
                    name=execution_name[:80],  # AWS name limit
                    input=json.dumps(sfn_input),
                )
                logger.info(f"Started Step Function execution: {sfn_res.get('executionArn')}")
            except Exception as e:
                logger.error(f"Failed to start Step Function execution for {job_id}", {"error": str(e)})
                raise e
        else:
            logger.warning("STATE_MACHINE_ARN not configured, skipping Step Function trigger.")

    return {"status": "SUCCESS", "processed_records": len(records)}
