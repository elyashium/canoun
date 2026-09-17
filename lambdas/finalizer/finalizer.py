import json
import os
import time
from decimal import Decimal
from typing import Any, Dict, List
import boto3
from shared.logger import StructuredLogger

logger = StructuredLogger("finalizer")

EVALUATIONS_TABLE = os.environ.get("EVALUATIONS_TABLE", "Evaluations")
PAGE_BUCKET_NAME = os.environ.get("PAGE_BUCKET_NAME", "evaluator-page-images")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
s3_client = boto3.client("s3", region_name=AWS_REGION)

def _convert_floats_to_decimals(obj: Any) -> Any:
    """DynamoDB requires Decimal types instead of Python float."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _convert_floats_to_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_floats_to_decimals(i) for i in obj]
    return obj

def cleanup_ephemeral_pages(job_id: str) -> None:
    """Delete ephemeral page PNGs from S3 page-images bucket to save costs."""
    prefix = f"pages/{job_id}/"
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=PAGE_BUCKET_NAME, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3_client.delete_objects(Bucket=PAGE_BUCKET_NAME, Delete={"Objects": objects})
        logger.info(f"Cleaned up ephemeral pages for job {job_id}")
    except Exception as e:
        logger.warning(f"Error cleaning up ephemeral pages for job {job_id}: {e}")

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    job_id = event["job_id"]
    user_id = event.get("user_id")
    rubric_id = event.get("rubric_id")
    sections_config = event.get("sections", [])
    scores = event.get("scores", [])

    logger.set_job_id(job_id)
    logger.info(f"Finalizing evaluation for job {job_id} with {len(scores)} evaluated questions")

    # Group questions by section
    section_map: Dict[str, List[Dict[str, Any]]] = {}
    for sc in scores:
        sec = sc.get("section", "General")
        section_map.setdefault(sec, []).append(sc)

    total_marks_awarded = 0.0
    max_total_marks = 0.0
    section_breakdown = {}
    all_failure_modes: Dict[str, int] = {}

    # Process each section, honoring 'attempt_any' rules
    for sec_conf in sections_config:
        sec_name = sec_conf.get("name", "General")
        items = section_map.get(sec_name, [])
        attempt_any = sec_conf.get("attempt_any")

        if attempt_any and len(items) > attempt_any:
            # Sort attempted questions by marks_awarded descending, pick top `attempt_any`
            attempted_items = [q for q in items if q.get("attempted", False)]
            attempted_items.sort(key=lambda x: x.get("marks_awarded", 0), reverse=True)
            selected = attempted_items[:attempt_any]
            # Mark others as extra attempt
            selected_ids = {q["question_id"] for q in selected}
            for q in items:
                if q["question_id"] not in selected_ids:
                    q["is_extra_attempt"] = True
                    q["marks_awarded_counted"] = 0.0
                else:
                    q["is_extra_attempt"] = False
                    q["marks_awarded_counted"] = q.get("marks_awarded", 0.0)
        else:
            selected = items
            for q in items:
                q["is_extra_attempt"] = False
                q["marks_awarded_counted"] = q.get("marks_awarded", 0.0)

        sec_awarded = sum(q.get("marks_awarded_counted", q.get("marks_awarded", 0.0)) for q in selected)
        sec_max = sum(q.get("max_marks", 0.0) for q in selected)

        total_marks_awarded += sec_awarded
        max_total_marks += sec_max

        section_breakdown[sec_name] = {
            "marks_awarded": round(sec_awarded, 1),
            "max_marks": round(sec_max, 1),
            "questions_evaluated": len(items),
        }

    # If no section config matched, sum all directly
    if not section_breakdown and scores:
        total_marks_awarded = sum(q.get("marks_awarded", 0.0) for q in scores)
        max_total_marks = sum(q.get("max_marks", 0.0) for q in scores)
        section_breakdown["General"] = {
            "marks_awarded": round(total_marks_awarded, 1),
            "max_marks": round(max_total_marks, 1),
            "questions_evaluated": len(scores),
        }

    # Aggregate failure modes
    for q in scores:
        for mode in q.get("failure_modes", []):
            all_failure_modes[mode] = all_failure_modes.get(mode, 0) + 1

    percentage = round((total_marks_awarded / max_total_marks * 100), 2) if max_total_marks > 0 else 0.0
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    evaluation_report = {
        "job_id": job_id,
        "user_id": user_id,
        "rubric_id": rubric_id,
        "status": "DONE",
        "total_marks_awarded": round(total_marks_awarded, 1),
        "max_total_marks": round(max_total_marks, 1),
        "percentage": percentage,
        "section_breakdown": section_breakdown,
        "questions": scores,
        "failure_mode_summary": all_failure_modes,
        "completed_at": now_iso,
        "updated_at": now_iso,
    }

    # Save to DynamoDB Evaluations table
    eval_table = dynamodb.Table(EVALUATIONS_TABLE)
    eval_table.put_item(Item=_convert_floats_to_decimals(evaluation_report))

    # Clean up ephemeral S3 page images
    cleanup_ephemeral_pages(job_id)

    logger.info(f"Job {job_id} successfully finalized with score {total_marks_awarded}/{max_total_marks} ({percentage}%)")

    return {
        "statusCode": 200,
        "job_id": job_id,
        "status": "DONE",
        "total_marks_awarded": total_marks_awarded,
        "max_total_marks": max_total_marks,
        "percentage": percentage,
    }
