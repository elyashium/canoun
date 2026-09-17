import json
import os
from typing import Dict, Any, List
import functools

import numpy as np
from fastembed import TextEmbedding
from sklearn.metrics.pairwise import cosine_similarity
import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.config import get_settings
from backend.schemas import ScorerOutput

class SemanticScorer:
    def __init__(self):
        self.settings = get_settings()
        self.use_bedrock = bool(os.environ.get("AWS_EXECUTION_ENV") or not self.settings.GROQ_API_KEY)
        
        if self.use_bedrock:
            self.bedrock_runtime = boto3.client("bedrock-runtime", region_name=self.settings.AWS_REGION)
        else:
            from groq import AsyncGroq
            import instructor
            self.client = instructor.from_groq(AsyncGroq(api_key=self.settings.GROQ_API_KEY), mode=instructor.Mode.JSON)

        # Use fastembed for lightweight embeddings (no PyTorch dependency)
        self.embedding_model = TextEmbedding("BAAI/bge-small-en-v1.5")

    @functools.lru_cache(maxsize=1000)
    def _get_embedding(self, text: str):
        return list(self.embedding_model.embed([text]))[0]

    def _compute_cosine_similarity(self, text_a: str, text_b: str) -> float:
        emb_a = self._get_embedding(text_a)
        emb_b = self._get_embedding(text_b)
        similarity = cosine_similarity([emb_a], [emb_b])[0][0]
        return float(np.clip(similarity, 0.0, 1.0))

    def _compute_key_concepts_ratio(
        self, student_answer: str, key_concepts: List[str]
    ) -> float:
        if not key_concepts:
            return 0.0

        student_emb = self._get_embedding(student_answer)
        matched = 0

        for concept in key_concepts:
            concept_emb = self._get_embedding(concept)
            sim = cosine_similarity([student_emb], [concept_emb])[0][0]
            if sim > 0.55:
                matched += 1

        return matched / len(key_concepts)

    @retry(
        stop=stop_after_attempt(4), 
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def _llm_judge_score(
        self,
        student_answer: str,
        ideal_answer: str,
        question: str,
        max_marks: int,
    ) -> ScorerOutput:
        judge_prompt = f"""You are an expert examiner evaluating a student's answer.

Question: {question}
Maximum Marks: {max_marks}
Ideal Answer: {ideal_answer}
Student's Answer: {student_answer}

Evaluate the student's answer against the ideal answer. Consider:
1. Factual accuracy
2. Completeness of key concepts covered
3. Clarity of explanation
4. Relevance to the question

Output JSON strictly adhering to schema:
{{
  "score_fraction": float,
  "reasoning": string,
  "missing_concepts": [string],
  "strengths": [string]
}}"""

        if self.use_bedrock:
            req_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "system": "You are a precise academic examiner. Respond ONLY with valid JSON.",
                "messages": [{"role": "user", "content": judge_prompt}],
                "temperature": 0.2,
            }
            res = self.bedrock_runtime.invoke_model(
                modelId=self.settings.BEDROCK_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(req_body),
            )
            data = json.loads(res["body"].read().decode("utf-8"))
            raw_text = data["content"][0]["text"].strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            parsed = json.loads(raw_text.strip())
            return ScorerOutput(**parsed)
        else:
            return await self.client.chat.completions.create(
                model=self.settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "You are a precise academic examiner."},
                    {"role": "user", "content": judge_prompt},
                ],
                response_model=ScorerOutput,
                temperature=0.2,
                max_tokens=1024,
            )

    async def score_answer(
        self,
        student_answer: str,
        ideal_answer: str,
        question: str,
        max_marks: int,
        key_concepts: List[str] = None,
    ) -> Dict[str, Any]:
        if not student_answer.strip():
            return {
                "marks_awarded": 0.0,
                "max_marks": max_marks,
                "semantic_similarity": 0.0,
                "key_concepts_ratio": 0.0,
                "llm_score_fraction": 0.0,
                "reasoning": "No answer provided.",
                "missing_concepts": key_concepts or [],
                "strengths": [],
            }

        key_concepts = key_concepts or []
        semantic_sim = self._compute_cosine_similarity(student_answer, ideal_answer)
        concepts_ratio = self._compute_key_concepts_ratio(student_answer, key_concepts)
        llm_output = await self._llm_judge_score(student_answer, ideal_answer, question, max_marks)

        # Blended score fraction
        blended_fraction = (
            self.settings.WEIGHT_SEMANTIC * semantic_sim
            + self.settings.WEIGHT_LLM * llm_output.score_fraction
        )
        # Boost if all key concepts are present
        if concepts_ratio >= 0.8:
            blended_fraction = min(1.0, blended_fraction + 0.05)

        marks_awarded = round(blended_fraction * max_marks, 1)

        return {
            "marks_awarded": marks_awarded,
            "max_marks": max_marks,
            "semantic_similarity": round(semantic_sim, 3),
            "key_concepts_ratio": round(concepts_ratio, 3),
            "llm_score_fraction": round(llm_output.score_fraction, 3),
            "reasoning": llm_output.reasoning,
            "missing_concepts": llm_output.missing_concepts,
            "strengths": llm_output.strengths,
        }
