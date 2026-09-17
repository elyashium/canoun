import json
import pytest
from shared.logger import mask_pii, sanitize_data, StructuredLogger

def test_mask_pii_patterns():
    # Aadhaar masking
    text = "Student Aadhaar is 1234 5678 9012 for verification."
    masked = mask_pii(text)
    assert "[REDACTED_AADHAAR]" in masked
    assert "1234 5678 9012" not in masked

    # Phone number masking
    text = "Contact guardian at +919876543210 or 9876543210."
    masked = mask_pii(text)
    assert "[REDACTED_PHONE]" in masked
    assert "9876543210" not in masked

    # Email masking
    text = "Email candidate at student@example.edu.in for marksheet."
    masked = mask_pii(text)
    assert "[REDACTED_EMAIL]" in masked
    assert "student@example.edu.in" not in masked

    # Roll Number masking
    text = "Answer sheet roll no: 8472910, please check."
    masked = mask_pii(text)
    assert "[REDACTED_ROLL_NO]" in masked

def test_sanitize_dict_keys():
    data = {
        "job_id": "job-123",
        "student_name": "Rohan Sharma",
        "roll_number": "1298471",
        "rubric_id": "cbse-physics-2025",
        "nested": {
            "candidate_name": "Priya Singh",
            "score": 45,
        }
    }
    sanitized = sanitize_data(data)
    assert sanitized["job_id"] == "job-123"
    assert sanitized["student_name"] == "[REDACTED]"
    assert sanitized["roll_number"] == "[REDACTED]"
    assert sanitized["nested"]["candidate_name"] == "[REDACTED]"
    assert sanitized["nested"]["score"] == 45
