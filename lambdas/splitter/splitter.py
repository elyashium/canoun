import os
import io
import json
from typing import Any, Dict, List
import boto3
import fitz  # PyMuPDF
from shared.logger import StructuredLogger

logger = StructuredLogger("splitter")

PAGE_BUCKET_NAME = os.environ.get("PAGE_BUCKET_NAME", "evaluator-page-images")
RUBRICS_TABLE = os.environ.get("RUBRICS_TABLE", "Rubrics")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
DEFAULT_MAX_PAGES = 64
TARGET_DPI = 200

s3_client = boto3.client("s3", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

class PageLimitExceededError(Exception):
    pass

class InvalidPDFError(Exception):
    pass

def get_max_pages_for_rubric(rubric_id: str) -> int:
    try:
        table = dynamodb.Table(RUBRICS_TABLE)
        res = table.get_item(Key={"rubric_id": rubric_id})
        item = res.get("Item")
        if item and "max_pages" in item:
            return int(item["max_pages"])
    except Exception as e:
        logger.warning(f"Could not load max_pages for rubric {rubric_id}: {e}")
    return DEFAULT_MAX_PAGES

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    job_id = event["job_id"]
    rubric_id = event.get("rubric_id", "default")
    raw_bucket = event["raw_bucket"]
    raw_key = event["raw_key"]

    logger.set_job_id(job_id)
    logger.info(f"Starting PDF split for s3://{raw_bucket}/{raw_key}")

    local_pdf_path = f"/tmp/{job_id}_booklet.pdf"

    # Download from S3
    try:
        s3_client.download_file(raw_bucket, raw_key, local_pdf_path)
    except Exception as e:
        logger.error(f"Failed to download raw PDF: {e}")
        raise e

    # Validate PDF magic header
    with open(local_pdf_path, "rb") as f:
        header = f.read(5)
        if not header.startswith(b"%PDF-"):
            raise InvalidPDFError(f"Uploaded file {raw_key} is not a valid PDF")

    max_pages = get_max_pages_for_rubric(rubric_id)

    doc = fitz.open(local_pdf_path)
    page_count = len(doc)
    logger.info(f"Document has {page_count} pages (allowed max: {max_pages})")

    if page_count > max_pages:
        doc.close()
        raise PageLimitExceededError(
            f"Booklet exceeds maximum permitted pages ({page_count} > {max_pages}). Submission rejected."
        )

    # 200 DPI Matrix: 72 points per inch in PDF -> 200 / 72 ≈ 2.77777778
    zoom = TARGET_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    page_items = []
    for page_idx in range(page_count):
        page = doc.load_page(page_idx)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img_bytes = pix.tobytes(output="png")

        dest_key = f"pages/{job_id}/page_{page_idx:03d}.png"
        s3_client.put_object(
            Bucket=PAGE_BUCKET_NAME,
            Key=dest_key,
            Body=img_bytes,
            ContentType="image/png",
            Metadata={
                "job-id": job_id,
                "page-index": str(page_idx),
                "page-number": str(page_idx + 1),
            },
        )
        page_items.append({
            "page_index": page_idx,
            "page_number": page_idx + 1,
            "page_s3_bucket": PAGE_BUCKET_NAME,
            "page_s3_key": dest_key,
        })

    doc.close()
    if os.path.exists(local_pdf_path):
        os.remove(local_pdf_path)

    logger.info(f"Successfully split and uploaded {len(page_items)} pages for job {job_id}")

    return {
        "job_id": job_id,
        "user_id": event.get("user_id"),
        "rubric_id": rubric_id,
        "total_pages": page_count,
        "pages": page_items,
    }
