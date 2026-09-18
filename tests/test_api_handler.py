import json
import time
import pytest
from lambdas.api_handler.handler import lambda_handler

class DummyContext:
    def get_remaining_time_in_millis(self):
        return 30000

class MockTable:
    def __init__(self, items=None):
        self.items = items or {}

    def get_item(self, Key):
        # Handle partition key or composite key
        if "job_id" in Key and "reviewer_id" in Key:
            key_tuple = (Key["job_id"], Key["reviewer_id"])
            return {"Item": self.items.get(key_tuple)}
        elif "job_id" in Key:
            return {"Item": self.items.get(Key["job_id"])}
        return {}

    def put_item(self, Item):
        if "reviewer_id" in Item:
            self.items[(Item["job_id"], Item["reviewer_id"])] = Item
        else:
            self.items[Item["job_id"]] = Item

def make_auth_context(user_id: str) -> dict:
    """Build a synthetic HTTP API JWT authorizer requestContext for a given user sub."""
    return {
        "authorizer": {
            "jwt": {
                "claims": {"sub": user_id}
            }
        }
    }

def test_api_handler_cors_options():
    event = {
        "httpMethod": "OPTIONS",
        "path": "/api/jobs",
    }
    resp = lambda_handler(event, DummyContext())
    assert resp["statusCode"] == 200
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"

def test_unauthenticated_request_returns_401():
    """With no Cognito claims in requestContext the handler must fail closed with 401."""
    event = {
        "httpMethod": "GET",
        "path": "/api/jobs/job-xyz",
        # No requestContext.authorizer at all
    }
    resp = lambda_handler(event, DummyContext())
    assert resp["statusCode"] == 401
    assert "Unauthorized" in resp["body"]

def test_missing_idempotency_key_returns_400():
    """POST /api/jobs without Idempotency-Key header must return 400."""
    event = {
        "httpMethod": "POST",
        "path": "/api/jobs",
        "requestContext": make_auth_context("teacher-abc"),
        "headers": {},
        "body": json.dumps({"rubric_id": "rubric-1"}),
    }
    resp = lambda_handler(event, DummyContext())
    assert resp["statusCode"] == 400
    assert "Idempotency-Key" in resp["body"]

def test_api_handler_create_job_missing_rubric():
    event = {
        "httpMethod": "POST",
        "path": "/api/jobs",
        "requestContext": make_auth_context("teacher-123"),
        "headers": {"Idempotency-Key": "key-abc"},
        "body": json.dumps({}),
    }
    resp = lambda_handler(event, DummyContext())
    assert resp["statusCode"] == 400
    assert "Missing required field" in resp["body"]

def test_reviewer_grants_authorization(monkeypatch):
    """
    Test authorization matrix:
    - Owner can read job
    - Non-owner without grant gets 403
    - Owner grants reviewer access
    - Granted reviewer can read job
    - Expired grant gets 403
    """
    owner_id = "teacher-owner"
    stranger_id = "teacher-stranger"
    reviewer_id = "teacher-reviewer"
    job_id = "job-eval-789"

    evaluations_mock = MockTable({
        job_id: {
            "job_id": job_id,
            "user_id": owner_id,
            "rubric_id": "cbse-physics-class12-2025",
            "status": "DONE",
            "total_marks_awarded": 68.0,
        }
    })

    grants_mock = MockTable()

    def mock_table_factory(table_name):
        if "ReviewerGrants" in table_name:
            return grants_mock
        return evaluations_mock

    monkeypatch.setattr("lambdas.api_handler.handler.dynamodb.Table", mock_table_factory)

    # 1. Owner requests their own job -> 200 OK
    event_owner = {
        "httpMethod": "GET",
        "path": f"/api/jobs/{job_id}",
        "requestContext": make_auth_context(owner_id),
    }
    resp = lambda_handler(event_owner, DummyContext())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["job_id"] == job_id
    assert data["user_id"] == owner_id

    # 2. Non-owner (stranger) requests job without grant -> 403 Forbidden
    event_stranger = {
        "httpMethod": "GET",
        "path": f"/api/jobs/{job_id}",
        "requestContext": make_auth_context(stranger_id),
    }
    resp_stranger = lambda_handler(event_stranger, DummyContext())
    assert resp_stranger["statusCode"] == 403
    assert "Forbidden" in resp_stranger["body"]

    # 3. Non-owner tries to grant access to someone else -> 403 Forbidden
    event_illegal_grant = {
        "httpMethod": "POST",
        "path": f"/api/jobs/{job_id}/grant",
        "requestContext": make_auth_context(stranger_id),
        "body": json.dumps({"reviewer_id": "someone-else"}),
    }
    resp_illegal = lambda_handler(event_illegal_grant, DummyContext())
    assert resp_illegal["statusCode"] == 403

    # 4. Owner grants reviewer access for 48 hours -> 200 OK
    event_grant = {
        "httpMethod": "POST",
        "path": f"/api/jobs/{job_id}/grant",
        "requestContext": make_auth_context(owner_id),
        "body": json.dumps({"reviewer_id": reviewer_id, "ttl_hours": 48}),
    }
    resp_grant = lambda_handler(event_grant, DummyContext())
    assert resp_grant["statusCode"] == 200
    assert "Granted reviewer" in resp_grant["body"]

    # 5. Granted reviewer now reads job -> 200 OK
    event_reviewer = {
        "httpMethod": "GET",
        "path": f"/api/jobs/{job_id}",
        "requestContext": make_auth_context(reviewer_id),
    }
    resp_reviewer = lambda_handler(event_reviewer, DummyContext())
    assert resp_reviewer["statusCode"] == 200
    data_rev = json.loads(resp_reviewer["body"])
    assert data_rev["job_id"] == job_id

    # 6. Test expired grant -> 403 Forbidden
    # Artificially set expires_at in the past
    grants_mock.items[(job_id, reviewer_id)]["expires_at"] = int(time.time()) - 100
    resp_expired = lambda_handler(event_reviewer, DummyContext())
    assert resp_expired["statusCode"] == 403
    assert "Forbidden" in resp_expired["body"]
