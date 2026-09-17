import pytest
from lambdas.aggregator.aggregator import lambda_handler

class DummyContext:
    def get_remaining_time_in_millis(self):
        return 30000

def test_aggregator_multi_page_stitching(monkeypatch):
    # Mock rubric load
    monkeypatch.setattr("lambdas.aggregator.aggregator.load_rubric", lambda r_id: {
        "sections": [{"name": "Section A", "max_marks": 5}],
        "questions": {
            "Q1": {
                "question_id": "Q1",
                "section": "Section A",
                "max_marks": 5,
                "model_answer": "Gauss's law statement.",
                "keywords": ["flux", "charge"],
            }
        }
    })

    event = {
        "job_id": "job-test-aggregator",
        "user_id": "user-teacher",
        "rubric_id": "cbse-physics-class12-2025",
        "ocr_results": [
            {
                "page_index": 0,
                "page_number": 1,
                "segments": [
                    {
                        "question_id_raw": "Q 1",
                        "question_id_normalized": "Q1",
                        "student_text": "Gauss's law states that total flux is q/eps0.",
                        "has_diagram": False,
                        "is_struck_through": False,
                        "confidence_ocr": 0.95,
                    }
                ]
            },
            {
                "page_index": 1,
                "page_number": 2,
                "segments": [
                    {
                        "question_id_raw": "Q 1 (contd)",
                        "question_id_normalized": "Q1",
                        "student_text": "Formula: Phi = oint E.dA = q / eps0.",
                        "has_diagram": True,
                        "is_struck_through": False,
                        "confidence_ocr": 0.98,
                    }
                ]
            }
        ]
    }

    res = lambda_handler(event, DummyContext())

    assert res["job_id"] == "job-test-aggregator"
    assert len(res["evaluation_items"]) == 1
    item = res["evaluation_items"][0]
    assert item["question_id"] == "Q1"
    assert item["attempted"] is True
    assert item["pages"] == [1, 2]
    assert item["has_diagram"] is True
    assert "Gauss's law" in item["student_answer"]
    assert "Formula: Phi = oint" in item["student_answer"]
