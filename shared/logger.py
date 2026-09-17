import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Sensitive PII patterns relevant to Indian Exam Answer Booklets and user submissions
PII_PATTERNS = [
    # 12-digit Aadhaar number (with or without spaces/hyphens)
    (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"), "[REDACTED_AADHAAR]"),
    # 10-digit Indian Mobile number starting with 6-9
    (re.compile(r"\b(?:\+91|91)?[6-9]\d{9}\b"), "[REDACTED_PHONE]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # CBSE / State Board Roll Number patterns (usually 7-9 consecutive digits in isolation or prefixed)
    (re.compile(r"\b(?:roll\s*(?:no|num|number)?[:\s\.-]*)(\d{6,10})\b", re.IGNORECASE), "Roll No: [REDACTED_ROLL_NO]"),
    # School / Exam Center Code (5-6 digits)
    (re.compile(r"\b(?:school|centre|center)\s*(?:code|no)?[:\s\.-]*(\d{4,6})\b", re.IGNORECASE), "Center/School: [REDACTED_CENTER_CODE]"),
]

def mask_pii(text: str) -> str:
    """Mask common PII patterns in logs and extracted text before logging."""
    if not isinstance(text, str):
        return text
    sanitized = text
    for pattern, replacement in PII_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized

def sanitize_data(data: Any) -> Any:
    """Recursively traverse dicts/lists to mask string values and sensitive keys."""
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if any(sensitive in k.lower() for sensitive in ["roll", "student_name", "candidate_name", "secret", "password", "token", "auth", "aadhaar"]):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = sanitize_data(v)
        return cleaned
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    elif isinstance(data, str):
        return mask_pii(data)
    return data

class StructuredLogger:
    """Structured JSON Logger for AWS Lambda & Step Functions with PII masking."""

    def __init__(self, service_name: str = "evaluator.ai", job_id: Optional[str] = None):
        self.service_name = service_name
        self.job_id = job_id
        self._logger = logging.getLogger(service_name)
        self._logger.setLevel(logging.INFO)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(logging.INFO)
            self._logger.addHandler(handler)

    def set_job_id(self, job_id: str) -> None:
        self.job_id = job_id

    def _format_record(self, level: str, message: str, extra: Optional[Dict[str, Any]] = None) -> str:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "service": self.service_name,
            "message": mask_pii(message),
        }
        if self.job_id:
            record["job_id"] = self.job_id
        if extra:
            record["context"] = sanitize_data(extra)
        return json.dumps(record, default=str)

    def info(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._logger.info(self._format_record("INFO", message, extra))

    def warning(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._logger.warning(self._format_record("WARNING", message, extra))

    def error(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._logger.error(self._format_record("ERROR", message, extra))

    def debug(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._logger.debug(self._format_record("DEBUG", message, extra))
