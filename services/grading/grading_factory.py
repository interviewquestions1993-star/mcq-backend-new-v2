from typing import Dict
from .base_grader import BaseGrader
from .simple_grader import SimpleGrader
from .semantic_grader import SemanticGrader
from .ai_grader import AIGrader

class GradingFactory:
    """
    Factory pattern to retrieve the correct grader instance based on the grading mode.
    """
    _graders: Dict[str, BaseGrader] = {}
    
    @classmethod
    def _initialize(cls):
        if not cls._graders:
            cls._graders = {
                "simple": SimpleGrader(),
                "semantic": SemanticGrader(),
                "ai": AIGrader()
            }
            
    @classmethod
    def get_grader(cls, mode: str) -> BaseGrader:
        """
        Retrieves the grader corresponding to the requested mode.
        Falls back to 'simple' if the mode is unrecognized.
        """
        cls._initialize()
        
        mode = mode.lower().strip()
        if mode not in cls._graders:
            import logging
            logging.getLogger(__name__).warning(f"Unrecognized grading mode '{mode}'. Defaulting to 'simple'.")
            return cls._graders["simple"]
            
        return cls._graders[mode]
