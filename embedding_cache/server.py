import os
import functools
from typing import List
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastembed import TextEmbedding
from sklearn.metrics.pairwise import cosine_similarity

app = FastAPI(title="Evaluator.ai Embedding Cache Sidecar", version="1.0.0")

class SimilarityRequest(BaseModel):
    text_a: str
    text_b: str

class SimilarityResponse(BaseModel):
    similarity: float

class EmbedRequest(BaseModel):
    texts: List[str]

class EmbedResponse(BaseModel):
    embeddings: List[List[float]]

# Initialize embedding model on import
MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")
embedding_model = TextEmbedding(MODEL_NAME)

@functools.lru_cache(maxsize=5000)
def get_embedding(text: str) -> np.ndarray:
    """In-memory LRU cache for embeddings."""
    return list(embedding_model.embed([text]))[0]

@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "cache_info": str(get_embedding.cache_info()),
    }

@app.post("/similarity", response_model=SimilarityResponse)
def compute_similarity(req: SimilarityRequest):
    if not req.text_a.strip() or not req.text_b.strip():
        return SimilarityResponse(similarity=0.0)
    emb_a = get_embedding(req.text_a)
    emb_b = get_embedding(req.text_b)
    sim = float(cosine_similarity([emb_a], [emb_b])[0][0])
    return SimilarityResponse(similarity=max(0.0, min(1.0, round(sim, 4))))

@app.post("/embed", response_model=EmbedResponse)
def embed_texts(req: EmbedRequest):
    results = [get_embedding(t).tolist() for t in req.texts]
    return EmbedResponse(embeddings=results)
