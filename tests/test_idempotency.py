import pytest
from botocore.exceptions import ClientError
from shared.idempotency import check_or_reserve_idempotency_key, save_idempotency_result

class MockDynamoClient:
    def __init__(self):
        self.store = {}

    def put_item(self, TableName, Item, ConditionExpression):
        key = Item["idempotency_key"]["S"]
        if "attribute_not_exists" in ConditionExpression and key in self.store:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "The conditional request failed"}},
                "PutItem"
            )
        self.store[key] = Item
        return {}

    def get_item(self, TableName, Key, ConsistentRead=True):
        key = Key["idempotency_key"]["S"]
        return {"Item": self.store.get(key, {})}

    def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        key = Key["idempotency_key"]["S"]
        if key in self.store:
            self.store[key]["status"] = ExpressionAttributeValues[":status"]
            self.store[key]["response_payload"] = ExpressionAttributeValues[":payload"]
        return {}

def test_idempotency_flow():
    client = MockDynamoClient()
    table = "IdempotencyKeys"
    key = "idem-key-999"
    job_id = "job-first-123"
    user_id = "user-abc"

    # 1. First request -> successfully reserves key
    is_new, existing = check_or_reserve_idempotency_key(
        table_name=table,
        idempotency_key=key,
        job_id=job_id,
        user_id=user_id,
        dynamodb_client=client
    )
    assert is_new is True
    assert existing is None

    # 2. Save result
    save_idempotency_result(
        table_name=table,
        idempotency_key=key,
        response_payload='{"job_id": "job-first-123", "status": "COMPLETED"}',
        dynamodb_client=client
    )

    # 3. Second request with same idempotency key -> returns cached result
    is_new_2, existing_2 = check_or_reserve_idempotency_key(
        table_name=table,
        idempotency_key=key,
        job_id="job-second-456",
        user_id=user_id,
        dynamodb_client=client
    )
    assert is_new_2 is False
    assert existing_2 is not None
    assert existing_2["job_id"] == "job-first-123"
    assert existing_2["status"] == "COMPLETED"
    assert 'job-first-123' in existing_2["response_payload"]
