import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class GradingCache:
    """
    In-memory cache for QA evaluations.
    """
    def __init__(self):
        self._cache = {}

    def _generate_key(self, question_id: str, user_answer: str, grading_mode: str) -> str:
        """
        Generates a SHA-256 hash for the cache key.
        """
        raw_key = f"{question_id}:{user_answer}:{grading_mode}".encode("utf-8")
        return hashlib.sha256(raw_key).hexdigest()

    def get(self, question_id: str, user_answer: str, grading_mode: str) -> Optional[dict]:
        """
        Retrieves a cached evaluation result.
        """
        key = self._generate_key(question_id, user_answer, grading_mode)
        if key in self._cache:
            logger.info(f"Cache Hit: {grading_mode} mode for question {question_id}")
            return self._cache[key]
        return None

    def set(self, question_id: str, user_answer: str, grading_mode: str, result: dict):
        """
        Stores an evaluation result in the cache.
        """
        key = self._generate_key(question_id, user_answer, grading_mode)
        self._cache[key] = result
        logger.debug(f"Cached result for {grading_mode} mode for question {question_id}")

# Singleton instance
grading_cache = GradingCache()
