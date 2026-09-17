import json
import os
import time
from typing import Any, Dict
import boto3
from shared.logger import StructuredLogger

logger = StructuredLogger("fail-handler")

EVALUATIONS_TABLE = os.environ.get("EVALUATIONS_TABLE", "Evaluations")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    job_id = event.get("job_id", "UNKNOWN")
    error_info = event.get("error", {})
    error_type = error_info.get("Error", "WorkflowError")
    error_cause = error_info.get("Cause", "An unexpected error occurred during execution.")

    logger.set_job_id(job_id)
    logger.error(f"Execution failed for job {job_id}: {error_type}", {"cause": error_cause})

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Update job in DynamoDB
    if job_id != "UNKNOWN":
        try:
            table = dynamodb.Table(EVALUATIONS_TABLE)
            table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #status = :status, #error = :error, #failed_at = :failed_at",
                ExpressionAttributeNames={
                    "#status": "status",
                    "#error": "error_details",
                    "#failed_at": "failed_at",
                },
                ExpressionAttributeValues={
                    ":status": "FAILED",
                    ":error": {
                        "error_type": error_type,
                        "error_message": str(error_cause)[:1000],
                    },
                    ":failed_at": now_iso,
                },
            )
            logger.info(f"Updated job {job_id} status to FAILED in DynamoDB")
        except Exception as e:
            logger.error(f"Failed to update job status to FAILED in DynamoDB: {e}")

    return {
        "job_id": job_id,
        "status": "FAILED",
        "error_type": error_type,
    }
