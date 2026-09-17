import pytest
from lambdas.finalizer.finalizer import lambda_handler

class DummyContext:
    def get_remaining_time_in_millis(self):
        return 30000

def test_attempt_any_logic(monkeypatch):
    # Mock DynamoDB and S3 inside finalizer
    monkeypatch.setattr("lambdas.finalizer.finalizer.cleanup_ephemeral_pages", lambda x: None)

    class MockTable:
        def __init__(self):
            self.saved_item = None
        def put_item(self, Item):
            self.saved_item = Item

    mock_table = MockTable()
    monkeypatch.setattr("lambdas.finalizer.finalizer.dynamodb.Table", lambda name: mock_table)

    event = {
        "job_id": "job-test-attempt-any",
        "user_id": "user-1",
        "rubric_id": "cbse-physics-class12-2025",
        "sections": [
            {
                "name": "Section C",
                "max_marks": 10,
                "attempt_any": 2,  # Student attempted 3, best 2 should count
            }
        ],
        "scores": [
            {
                "question_id": "Q5",
                "section": "Section C",
                "attempted": True,
                "marks_awarded": 3.0,
                "max_marks": 5.0,
                "failure_modes": ["MISSING_UNITS"],
            },
            {
                "question_id": "Q6",
                "section": "Section C",
                "attempted": True,
                "marks_awarded": 5.0,
                "max_marks": 5.0,
                "failure_modes": [],
            },
            {
                "question_id": "Q7",
                "section": "Section C",
                "attempted": True,
                "marks_awarded": 4.5,
                "max_marks": 5.0,
                "failure_modes": ["ARITHMETIC_ERROR"],
            },
        ]
    }

    res = lambda_handler(event, DummyContext())

    assert res["status"] == "DONE"
    # Q6 (5.0) + Q7 (4.5) = 9.5 out of 10.0 max marks. Q5 (3.0) marked as extra attempt
    assert res["total_marks_awarded"] == 9.5
    assert res["max_total_marks"] == 10.0
    assert res["percentage"] == 95.0

    saved = mock_table.saved_item
    assert saved["failure_mode_summary"]["MISSING_UNITS"] == 1
    assert saved["failure_mode_summary"]["ARITHMETIC_ERROR"] == 1
