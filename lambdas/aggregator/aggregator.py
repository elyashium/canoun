import json
import os
from typing import Any, Dict, List
import boto3
from shared.logger import StructuredLogger

logger = StructuredLogger("aggregator")

RUBRICS_TABLE = os.environ.get("RUBRICS_TABLE", "Rubrics")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

def load_rubric(rubric_id: str) -> Dict[str, Any]:
    """Fetch rubric from DynamoDB or fallback to bundled default."""
    try:
        table = dynamodb.Table(RUBRICS_TABLE)
        res = table.get_item(Key={"rubric_id": rubric_id})
        item = res.get("Item")
        if item and "rubric_data" in item:
            data = item["rubric_data"]
            if isinstance(data, str):
                return json.loads(data)
            return data
    except Exception as e:
        logger.warning(f"Could not load rubric {rubric_id} from DynamoDB: {e}")

    # Fallback to local file if available in rubrics/ or data/
    for candidate in [
        f"rubrics/{rubric_id}.json",
        f"data/{rubric_id}.json",
        "data/answer_key.json",
    ]:
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    return {"questions": {}, "sections": []}

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Aggregates OCR segments across pages and matches them to rubric questions.
    """
    job_id = event["job_id"]
    rubric_id = event.get("rubric_id", "cbse-physics-class12-2025")
    ocr_results = event.get("ocr_results", [])

    logger.set_job_id(job_id)
    logger.info(f"Aggregating {len(ocr_results)} pages for job {job_id} using rubric {rubric_id}")

    rubric = load_rubric(rubric_id)
    rubric_questions = rubric.get("questions", {})
    # If questions is a list, convert to dict keyed by question_id
    if isinstance(rubric_questions, list):
        rubric_questions = {q.get("question_id", f"Q{i+1}"): q for i, q in enumerate(rubric_questions)}

    # Group extracted segments by normalized question_id
    grouped_answers: Dict[str, Dict[str, Any]] = {}

    for page_res in sorted(ocr_results, key=lambda x: x.get("page_index", 0)):
        page_num = page_res.get("page_number", 1)
        for seg in page_res.get("segments", []):
            qid = seg.get("question_id_normalized", "UNKNOWN")
            if qid == "UNKNOWN":
                continue

            text = seg.get("student_text", "").strip()
            is_struck = seg.get("is_struck_through", False)
            has_diagram = seg.get("has_diagram", False)
            ocr_conf = float(seg.get("confidence_ocr", 0.9))

            if qid not in grouped_answers:
                grouped_answers[qid] = {
                    "question_id": qid,
                    "pages": [page_num],
                    "text_parts": [text] if not is_struck else [],
                    "struck_text_parts": [text] if is_struck else [],
                    "has_diagram": has_diagram,
                    "ocr_confidences": [ocr_conf],
                }
            else:
                entry = grouped_answers[qid]
                if page_num not in entry["pages"]:
                    entry["pages"].append(page_num)
                if not is_struck:
                    entry["text_parts"].append(text)
                else:
                    entry["struck_text_parts"].append(text)
                entry["has_diagram"] = entry["has_diagram"] or has_diagram
                entry["ocr_confidences"].append(ocr_conf)

    # Build final list of evaluation items for scoring
    evaluation_items = []

    for qid, r_info in rubric_questions.items():
        ans_entry = grouped_answers.get(qid)
        if ans_entry and ans_entry["text_parts"]:
            full_text = "\n\n".join(ans_entry["text_parts"])
            attempted = True
            conf = sum(ans_entry["ocr_confidences"]) / len(ans_entry["ocr_confidences"])
            pages = ans_entry["pages"]
            has_diag = ans_entry["has_diagram"]
        elif ans_entry and ans_entry["struck_text_parts"]:
            # Student struck it out, evaluate with note
            full_text = "\n\n".join(ans_entry["struck_text_parts"]) + " [NOTE: Struck through by candidate]"
            attempted = True
            conf = sum(ans_entry["ocr_confidences"]) / len(ans_entry["ocr_confidences"])
            pages = ans_entry["pages"]
            has_diag = ans_entry["has_diagram"]
        else:
            full_text = ""
            attempted = False
            conf = 1.0
            pages = []
            has_diag = False

        evaluation_items.append({
            "job_id": job_id,
            "question_id": qid,
            "section": r_info.get("section", "General"),
            "max_marks": r_info.get("max_marks", 5),
            "step_marks": r_info.get("step_marks", []),
            "model_answer": r_info.get("model_answer", r_info.get("ideal_answer", "")),
            "canonical_keywords": r_info.get("keywords", []),
            "student_answer": full_text,
            "attempted": attempted,
            "pages": pages,
            "has_diagram": has_diag,
            "ocr_confidence": round(conf, 3),
        })

    logger.info(f"Aggregated {len(evaluation_items)} questions ready for evaluation")

    return {
        "job_id": job_id,
        "user_id": event.get("user_id"),
        "rubric_id": rubric_id,
        "sections": rubric.get("sections", []),
        "evaluation_items": evaluation_items,
    }
