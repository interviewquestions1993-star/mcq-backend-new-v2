import logging
from typing import Optional
from .base_grader import BaseGrader, GradingResult

logger = logging.getLogger(__name__)

class SemanticGrader(BaseGrader):
    def __init__(self):
        self._model = None

    @property
    def mode_name(self) -> str:
        return "semantic"
        
    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info("Loading SentenceTransformer model...")
                self._model = SentenceTransformer('all-MiniLM-L6-v2')
            except ImportError:
                raise RuntimeError("sentence-transformers is not installed")
        return self._model

    def grade(self, question_data: dict, user_answer: str) -> GradingResult:
        expected_answer = question_data.get("answer", "")
        eval_data = question_data.get("evaluation", {})
        min_similarity = eval_data.get("minimum_similarity", 0.70)
        must_have = eval_data.get("must_have_keywords", [])
        optional = eval_data.get("optional_keywords", [])
        max_marks = question_data.get("marks", 1)
        
        try:
            from sentence_transformers import util
            model = self._get_model()
            user_emb = model.encode(user_answer, convert_to_tensor=True)
            exp_emb = model.encode(expected_answer, convert_to_tensor=True)
            sim_score = util.cos_sim(user_emb, exp_emb).item()
        except Exception as e:
            logger.error(f"Semantic grading failed: {e}")
            raise e
        
        user_text_clean = user_answer.strip().lower()
        matched_must = sum(1 for kw in must_have if kw.lower() in user_text_clean)
        matched_opt = sum(1 for kw in optional if kw.lower() in user_text_clean)
        
        total_must = len(must_have)
        total_opt = len(optional)
        
        keyword_score = 0.0
        missing_keywords = []
        
        if total_must > 0 or total_opt > 0:
            if total_must > 0:
                must_score = matched_must / total_must
                for kw in must_have:
                    if kw.lower() not in user_text_clean:
                        missing_keywords.append(kw)
            else:
                must_score = 1.0
                
            if total_opt > 0:
                opt_score = matched_opt / total_opt
                for kw in optional:
                    if kw.lower() not in user_text_clean:
                        missing_keywords.append(kw)
            else:
                opt_score = 1.0
                
            if total_must > 0 and total_opt > 0:
                keyword_score = (must_score * 0.7) + (opt_score * 0.3)
            else:
                keyword_score = must_score if total_must > 0 else opt_score
        else:
            keyword_score = 1.0
            
        final_score = (sim_score * 0.7) + (keyword_score * 0.3)
        
        if final_score < min_similarity:
            marks_awarded = 0.0
            feedback = "Your answer missed the core concepts."
            overall = "Poor"
        else:
            if final_score >= 0.95:
                marks_awarded = max_marks
                feedback = "Excellent! You correctly explained the main concepts."
                overall = "Excellent"
            else:
                scaled = 0.5 + 0.5 * ((final_score - min_similarity) / (1.0 - min_similarity))
                marks_awarded = round(scaled * max_marks * 2) / 2
                feedback = "Your answer is mostly correct but could be improved by including more specific details."
                overall = "Good"
                
        if missing_keywords:
            feedback += " Missing concepts: " + ", ".join(missing_keywords)
            
        return GradingResult(
            marksAwarded=float(marks_awarded),
            maxMarks=float(max_marks),
            similarity=float(sim_score),
            keywordCoverage=float(keyword_score),
            feedback=feedback,
            missingKeywords=missing_keywords,
            strengths=[],
            overall=overall,
            evaluator="Semantic AI (MiniLM)"
        )
