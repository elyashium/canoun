import time
from typing import Any, Dict, Optional, Tuple
import boto3
from botocore.exceptions import ClientError
from shared.logger import StructuredLogger

logger = StructuredLogger("idempotency")

def check_or_reserve_idempotency_key(
    table_name: str,
    idempotency_key: str,
    job_id: str,
    user_id: str,
    ttl_hours: int = 24,
    dynamodb_client: Optional[Any] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Conditionally reserves an idempotency key in DynamoDB.
    Returns:
        (is_new_request, existing_record)
        If is_new_request is True: Key successfully reserved, proceed with processing.
        If is_new_request is False: Key already exists, returned existing_record contains cached payload.
    """
    ddb = dynamodb_client or boto3.client("dynamodb")
    now = int(time.time())
    ttl_timestamp = now + (ttl_hours * 3600)

    try:
        ddb.put_item(
            TableName=table_name,
            Item={
                "idempotency_key": {"S": idempotency_key},
                "job_id": {"S": job_id},
                "user_id": {"S": user_id},
                "status": {"S": "IN_PROGRESS"},
                "created_at": {"N": str(now)},
                "ttl": {"N": str(ttl_timestamp)},
            },
            ConditionExpression="attribute_not_exists(idempotency_key)",
        )
        return True, None
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Key already exists, retrieve existing record
            logger.info(f"Duplicate request detected for idempotency_key={idempotency_key}")
            res = ddb.get_item(
                TableName=table_name,
                Key={"idempotency_key": {"S": idempotency_key}},
                ConsistentRead=True,
            )
            item = res.get("Item", {})
            existing = {
                "idempotency_key": item.get("idempotency_key", {}).get("S"),
                "job_id": item.get("job_id", {}).get("S"),
                "user_id": item.get("user_id", {}).get("S"),
                "status": item.get("status", {}).get("S"),
                "response_payload": item.get("response_payload", {}).get("S"),
            }
            return False, existing
        raise e

def save_idempotency_result(
    table_name: str,
    idempotency_key: str,
    response_payload: str,
    dynamodb_client: Optional[Any] = None,
) -> None:
    """Updates the idempotency key with completed response payload."""
    ddb = dynamodb_client or boto3.client("dynamodb")
    try:
        ddb.update_item(
            TableName=table_name,
            Key={"idempotency_key": {"S": idempotency_key}},
            UpdateExpression="SET #status = :status, #payload = :payload",
            ExpressionAttributeNames={
                "#status": "status",
                "#payload": "response_payload",
            },
            ExpressionAttributeValues={
                ":status": {"S": "COMPLETED"},
                ":payload": {"S": response_payload},
            },
        )
    except ClientError as e:
        logger.error(f"Failed to update idempotency result for key {idempotency_key}", {"error": str(e)})
