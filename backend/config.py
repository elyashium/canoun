from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    # Optional Groq for legacy/local testing
    GROQ_API_KEY: Optional[str] = Field(default="", description="Groq API key (optional for local/legacy)")
    VISION_MODEL: str = Field(default="qwen/qwen3.6-27b")
    LLM_MODEL: str = Field(default="llama-3.3-70b-versatile")
    EMBEDDING_MODEL: str = Field(default="all-MiniLM-L6-v2")
    WEIGHT_SEMANTIC: float = Field(default=0.4)
    WEIGHT_LLM: float = Field(default=0.6)
    MAX_FILE_SIZE_MB: int = Field(default=100)
    TEMP_DIR: str = Field(default="./temp_uploads")

    # AWS Environment Variables
    AWS_REGION: str = Field(default="ap-south-1")
    BEDROCK_MODEL_ID: str = Field(default="anthropic.claude-3-5-haiku-20241022-v1:0")
    RAW_BUCKET_NAME: str = Field(default="evaluator-raw-booklets")
    PAGE_BUCKET_NAME: str = Field(default="evaluator-page-images")
    ASYNC_RESULTS_BUCKET_NAME: str = Field(default="evaluator-async-results")
    EVALUATIONS_TABLE: str = Field(default="Evaluations")
    IDEMPOTENCY_TABLE: str = Field(default="IdempotencyKeys")
    RUBRICS_TABLE: str = Field(default="Rubrics")
    REVIEWER_GRANTS_TABLE: str = Field(default="ReviewerGrants")
    STATE_MACHINE_ARN: Optional[str] = Field(default="")
    EMBEDDING_CACHE_URL: Optional[str] = Field(default="")
    SAGEMAKER_ENDPOINT_NAME: Optional[str] = Field(default="")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    return Settings()