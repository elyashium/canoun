import base64
import json
import os
import re
from typing import Any, Dict, List
import boto3
from shared.logger import StructuredLogger

logger = StructuredLogger("ocr-worker")

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

s3_client = boto3.client("s3", region_name=AWS_REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=AWS_REGION)

def normalize_question_id(raw_id: str) -> str:
    """Normalize identifiers like 'Q 1. (a)' -> 'Q1A' or 'Section B Q12' -> 'Q12'."""
    if not raw_id:
        return "UNKNOWN"
    cleaned = raw_id.upper().strip()
    match = re.search(r"(?:Q|QUESTION)?\s*(\d+)\s*(?:\.|\-|\:)?\s*\(?([A-Z])?\)?", cleaned)
    if match:
        num = match.group(1)
        sub = match.group(2) or ""
        return f"Q{num}{sub}".strip()
    return re.sub(r"[^A-Z0-9]", "", cleaned)

OCR_SYSTEM_PROMPT = """You are an expert OCR and transcription engine specialized in evaluating Indian handwritten examination answer sheets (e.g. CBSE, ICSE, State Boards).
Your task is to transcribe handwritten and printed student answers with absolute fidelity from this answer booklet page.
Extract:
1. Question number / identifier as written by the student (e.g., 'Q 1(a)', 'Ans 4', 'Section B - 12')
2. Complete student response text (including equations, step derivations, and code)
3. Note if any diagram or graph is present
4. Note if any portion is crossed out / struck through
5. Your OCR confidence (0.0 to 1.0)

Respond ONLY with valid, unescaped JSON matching this schema:
{
  "segments": [
    {
      "question_id_raw": "string",
      "student_text": "string",
      "has_diagram": boolean,
      "diagram_description": "string or null",
      "is_struck_through": boolean,
      "confidence_ocr": number
    }
  ],
  "page_notes": "string"
}
"""

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    job_id = event.get("job_id", "")
    page_index = event.get("page_index", 0)
    page_number = event.get("page_number", page_index + 1)
    bucket = event["page_s3_bucket"]
    key = event["page_s3_key"]

    logger.set_job_id(job_id)
    logger.info(f"Extracting OCR for page {page_number} (key: {key})")

    # Download image bytes
    s3_obj = s3_client.get_object(Bucket=bucket, Key=key)
    image_bytes = s3_obj["Body"].read()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # Prepare Bedrock request payload (Anthropic Claude 3 / 3.5 format)
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": OCR_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Transcribe all answers on page {page_number} faithfully. Output strict JSON only.",
                    },
                ],
            }
        ],
        "temperature": 0.1,
    }

    try:
        response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )
        response_body = json.loads(response["body"].read().decode("utf-8"))
        text_content = response_body["content"][0]["text"].strip()

        # Clean JSON markdown fences if returned
        if text_content.startswith("```json"):
            text_content = text_content[7:]
        if text_content.startswith("```"):
            text_content = text_content[3:]
        if text_content.endswith("```"):
            text_content = text_content[:-3]
        text_content = text_content.strip()

        parsed = json.loads(text_content)
        segments = parsed.get("segments", [])
    except Exception as e:
        logger.error(f"Bedrock OCR invocation failed on page {page_number}: {e}")
        # Graceful fallback: return empty segments or placeholder error segment
        segments = [
            {
                "question_id_raw": "UNKNOWN",
                "question_id_normalized": "UNKNOWN",
                "student_text": f"[OCR extraction error: {str(e)}]",
                "has_diagram": False,
                "is_struck_through": False,
                "confidence_ocr": 0.0,
            }
        ]

    for seg in segments:
        seg["question_id_normalized"] = normalize_question_id(seg.get("question_id_raw", ""))
        seg["page_number"] = page_number

    return {
        "job_id": job_id,
        "page_index": page_index,
        "page_number": page_number,
        "segments": segments,
    }
