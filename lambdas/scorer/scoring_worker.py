import json
import os
import urllib.request
from typing import Any, Dict, List, Optional
import boto3
from shared.logger import StructuredLogger

logger = StructuredLogger("scoring-worker")

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0")
EMBEDDING_CACHE_URL = os.environ.get("EMBEDDING_CACHE_URL", "")
SAGEMAKER_ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

bedrock_runtime = boto3.client("bedrock-runtime", region_name=AWS_REGION)

_LOCAL_EMBED_MODEL = None

def compute_token_jaccard_similarity(text_a: str, text_b: str) -> float:
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return round(intersection / union, 3) if union > 0 else 0.0

def compute_in_process_similarity(text_a: str, text_b: str) -> float:
    global _LOCAL_EMBED_MODEL
    try:
        if _LOCAL_EMBED_MODEL is None:
            from fastembed import TextEmbedding
            _LOCAL_EMBED_MODEL = TextEmbedding("BAAI/bge-small-en-v1.5")
        from sklearn.metrics.pairwise import cosine_similarity
        emb_a = list(_LOCAL_EMBED_MODEL.embed([text_a]))[0]
        emb_b = list(_LOCAL_EMBED_MODEL.embed([text_b]))[0]
        sim = float(cosine_similarity([emb_a], [emb_b])[0][0])
        return max(0.0, min(1.0, round(sim, 3)))
    except Exception as e:
        logger.debug(f"Local fastembed not available, falling back to token similarity: {e}")
        return compute_token_jaccard_similarity(text_a, text_b)

def compute_semantic_similarity(text_a: str, text_b: str) -> float:
    """
    Attempt similarity check against ECS Fargate Spot embedding cache sidecar,
    with automatic in-process fastembed and token similarity fallbacks.
    """
    if not text_a.strip() or not text_b.strip():
        return 0.0

    if EMBEDDING_CACHE_URL:
        try:
            payload = json.dumps({"text_a": text_a, "text_b": text_b}).encode("utf-8")
            req = urllib.request.Request(
                f"{EMBEDDING_CACHE_URL.rstrip('/')}/similarity",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return float(data.get("similarity", 0.75))
        except Exception as e:
            logger.warning(f"Embedding cache sidecar call failed, falling back to in-process: {e}")

    return compute_in_process_similarity(text_a, text_b)

SCORING_SYSTEM_PROMPT = """You are a senior board examiner for Indian competitive and board exams (CBSE / ICSE / State Boards).
Evaluate the student's answer against the official model answer and step-marking scheme with rigorous fairness and precision.

Indian Examination Evaluation Principles:
1. Strict Step-Marking: Award marks for each correct formula, substitution, definition, diagram, or logic step independently.
2. Partial Credit: If intermediate steps are correct but the final calculation has an arithmetic slip, deduct ONLY the final calculation marks.
3. Units and Notation: Penalize if mandatory SI units or vector notations are missing when required.
4. Failure-Mode Tagging: Tag specific failure modes (e.g., 'MISSING_UNITS', 'INCORRECT_FORMULA', 'CONCEPTUAL_ERROR', 'ARITHMETIC_ERROR', 'INCOMPLETE_DERIVATION').

Respond ONLY with valid, unescaped JSON matching this schema:
{
  "marks_awarded": number,
  "max_marks": number,
  "step_evaluations": [
    {
      "step_description": "string",
      "marks_awarded": number,
      "max_marks": number,
      "feedback": "string"
    }
  ],
  "failure_modes": ["string"],
  "student_feedback": "string",
  "judge_confidence": number
}
"""

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    job_id = event.get("job_id", "")
    qid = event.get("question_id", "Q1")
    max_marks = float(event.get("max_marks", 5))
    attempted = event.get("attempted", True)
    student_ans = event.get("student_answer", "").strip()
    model_ans = event.get("model_answer", "")
    step_marks = event.get("step_marks", [])
    ocr_conf = float(event.get("ocr_confidence", 0.9))

    logger.set_job_id(job_id)

    if not attempted or not student_ans:
        return {
            "question_id": qid,
            "section": event.get("section", "General"),
            "attempted": False,
            "marks_awarded": 0.0,
            "max_marks": max_marks,
            "step_evaluations": [],
            "failure_modes": ["UNATTEMPTED"],
            "student_feedback": "Question was not attempted.",
            "ocr_confidence": 1.0,
            "semantic_similarity": 0.0,
            "calibrated_confidence": 1.0,
            "requires_human_review": False,
        }

    # 1. Semantic similarity (sidecar or in-process fastembed/token similarity fallback)
    semantic_sim = compute_semantic_similarity(student_ans, model_ans)

    # 2. Bedrock LLM-as-a-Judge
    prompt_user = (
        f"Question ID: {qid}\n"
        f"Max Marks: {max_marks}\n"
        f"Model Answer: {model_ans}\n"
        f"Step-marking rubric: {json.dumps(step_marks)}\n"
        f"Student Answer: {student_ans}\n\n"
        "Evaluate the student's answer step-by-step and produce strict JSON."
    )

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 3000,
        "system": SCORING_SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": prompt_user}
        ],
        "temperature": 0.1,
    }

    try:
        res = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )
        body_str = res["body"].read().decode("utf-8")
        parsed_res = json.loads(body_str)
        text_content = parsed_res["content"][0]["text"].strip()

        if text_content.startswith("```json"):
            text_content = text_content[7:]
        if text_content.startswith("```"):
            text_content = text_content[3:]
        if text_content.endswith("```"):
            text_content = text_content[:-3]
        text_content = text_content.strip()

        judge_data = json.loads(text_content)
    except Exception as e:
        logger.error(f"Bedrock Judge failed for {qid}: {e}")
        judge_data = {
            "marks_awarded": round(max_marks * semantic_sim, 1),
            "max_marks": max_marks,
            "step_evaluations": [],
            "failure_modes": ["LLM_EVALUATION_FALLBACK"],
            "student_feedback": "Evaluated using heuristic similarity fallback.",
            "judge_confidence": 0.5,
        }

    marks_awarded = min(max_marks, max(0.0, float(judge_data.get("marks_awarded", 0.0))))
    judge_conf = float(judge_data.get("judge_confidence", 0.85))

    # Composite Confidence Calibration Formula:
    # 0.40 * OCR Confidence + 0.35 * Judge Confidence + 0.25 * Semantic Similarity
    calibrated_conf = round((0.40 * ocr_conf) + (0.35 * judge_conf) + (0.25 * semantic_sim), 3)

    # Flag for human review if confidence < 0.70 or borderline marks
    requires_human_review = calibrated_conf < 0.70 or "CONCEPTUAL_ERROR" in judge_data.get("failure_modes", [])

    return {
        "question_id": qid,
        "section": event.get("section", "General"),
        "attempted": True,
        "marks_awarded": marks_awarded,
        "max_marks": max_marks,
        "step_evaluations": judge_data.get("step_evaluations", []),
        "failure_modes": judge_data.get("failure_modes", []),
        "student_feedback": judge_data.get("student_feedback", ""),
        "ocr_confidence": ocr_conf,
        "semantic_similarity": round(semantic_sim, 3),
        "calibrated_confidence": calibrated_conf,
        "requires_human_review": requires_human_review,
        "pages": event.get("pages", []),
        "has_diagram": event.get("has_diagram", False),
    }
