import json
import os
import time
from typing import Any, Dict, Optional
import boto3
from botocore.exceptions import ClientError
from shared.logger import StructuredLogger

logger = StructuredLogger("secrets")

_CACHE: Dict[str, Any] = {}
_CACHE_EXPIRY: Dict[str, float] = {}
DEFAULT_CACHE_TTL_SEC = 300  # 5 minutes

def get_secret(secret_name: str, cache_ttl: int = DEFAULT_CACHE_TTL_SEC, client: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch JSON secret from AWS Secrets Manager with in-memory caching.
    Falls back to environment variable if Secrets Manager is unavailable or in local dev mode.
    """
    # Check local environment variable first for rapid local testing / override
    env_override = os.environ.get(secret_name.upper())
    if env_override:
        try:
            return json.loads(env_override)
        except Exception:
            return {"value": env_override}

    now = time.time()
    if secret_name in _CACHE and now < _CACHE_EXPIRY.get(secret_name, 0):
        return _CACHE[secret_name]

    sm = client or boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    try:
        res = sm.get_secret_value(SecretId=secret_name)
        secret_string = res.get("SecretString")
        if secret_string:
            parsed = json.loads(secret_string)
            _CACHE[secret_name] = parsed
            _CACHE_EXPIRY[secret_name] = now + cache_ttl
            return parsed
    except ClientError as e:
        logger.warning(f"Could not retrieve secret {secret_name} from Secrets Manager: {e}")
    except Exception as e:
        logger.error(f"Error parsing secret {secret_name}: {e}")

    return None
