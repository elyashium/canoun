import base64
import io
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Any

from PIL import Image
import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.config import get_settings

class VisionExtractor:
    def __init__(self):
        self.settings = get_settings()
        self.use_bedrock = bool(os.environ.get("AWS_EXECUTION_ENV") or not self.settings.GROQ_API_KEY)
        if self.use_bedrock:
            self.bedrock_runtime = boto3.client("bedrock-runtime", region_name=self.settings.AWS_REGION)
        else:
            from groq import AsyncGroq
            self.groq_client = AsyncGroq(api_key=self.settings.GROQ_API_KEY)

    def _compress_and_encode_image(self, image_path: str) -> str:
        """Resizes large images and converts to optimal base64 JPEG/PNG."""
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _parse_json_response(self, raw_text: str) -> List[Dict[str, Any]]:
        cleaned = re.sub(r"```(?:json)?", "", raw_text, flags=re.IGNORECASE).strip()
        cleaned = cleaned.strip("`").strip()

        try:
            data = json.loads(cleaned)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("segments", "answers", "questions", "results"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                if "question_id" in data:
                    return [data]
        except json.JSONDecodeError:
            pass

        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    async def extract_and_segment(self, image_path: str) -> List[Dict[str, Any]]:
        base64_image = self._compress_and_encode_image(image_path)

        prompt = """You are an expert OCR system for handwritten answer sheets.
Look at this image and extract every handwritten answer.
Respond with ONLY a JSON array — no markdown, no extra text.
Format:
[
  {
    "question_id": "Q1",
    "extracted_text": "extracted student answer"
  }
]
"""

        if self.use_bedrock:
            req_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": base64_image,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "temperature": 0.1,
            }
            res = self.bedrock_runtime.invoke_model(
                modelId=self.settings.BEDROCK_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(req_body),
            )
            data = json.loads(res["body"].read().decode("utf-8"))
            raw_text = data["content"][0]["text"].strip()
            return self._parse_json_response(raw_text)
        else:
            response = await self.groq_client.chat.completions.create(
                model=self.settings.VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                            },
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            raw_text = response.choices[0].message.content.strip()
            return self._parse_json_response(raw_text)
