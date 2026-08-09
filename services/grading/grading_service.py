import logging
import time
from typing import Dict, Any

from utils.text_normalizer import normalize_text
from services.cache.grading_cache import grading_cache
from .grading_factory import GradingFactory
from .base_grader import GradingResult

logger = logging.getLogger(__name__)

class GradingService:
    """
    Orchestrates the entire grading pipeline, including text normalization, caching,
    grader selection, and graceful fallback.
    """
    
    @staticmethod
    def evaluate_answer(question_id: str, chapter: str, version: str, question_data: dict, user_answer: str, preferred_mode: str) -> GradingResult:
        # 1. Normalize the answer
        normalized_answer = normalize_text(user_answer)
        
        # 2. Check the cache
        cached_result = grading_cache.get(question_id, normalized_answer, preferred_mode)
        if cached_result:
            return cached_result
            
        start_time = time.time()
        result = None
        used_mode = preferred_mode
        
        # 3. Define fallback chain based on preferred mode
        fallback_chain = []
        if preferred_mode == "ai":
            fallback_chain = ["ai", "simple"]
        else:
            fallback_chain = ["simple"]
            
        # 4. Attempt grading with fallback
        for mode in fallback_chain:
            used_mode = mode
            try:
                grader = GradingFactory.get_grader(mode)
                result = grader.grade(question_data, normalized_answer)
                break # Success!
            except Exception as e:
                logger.warning(f"{mode.upper()} evaluation failed. Reason: {e}")
                if mode != fallback_chain[-1]:
                    next_mode = fallback_chain[fallback_chain.index(mode) + 1]
                    logger.warning(f"Falling back to {next_mode.upper()} evaluator.")
                else:
                    logger.error("All grading evaluators failed.")
                    raise RuntimeError("Failed to grade answer using all available evaluators.")
                    
        eval_time = time.time() - start_time
        
        # 5. Log the outcome
        logger.info(f"Question ID: {question_id} | Chapter: {chapter} | Mode: {used_mode.upper()} | "
                    f"Cache Hit: No | Evaluation Time: {eval_time:.2f} sec | "
                    f"Marks: {result['marksAwarded']}/{result['maxMarks']}")
                    
        # 6. Cache the successful result
        grading_cache.set(question_id, normalized_answer, preferred_mode, result)
        
        return result
