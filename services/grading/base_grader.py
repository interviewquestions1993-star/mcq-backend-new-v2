from typing import List, TypedDict, Any
from abc import ABC, abstractmethod

class GradingResult(TypedDict):
    marksAwarded: float
    maxMarks: float
    similarity: float
    keywordCoverage: float
    feedback: str
    missingKeywords: List[str]
    strengths: List[str]
    overall: str
    evaluator: str
    
class BaseGrader(ABC):
    """
    Abstract base class for all answer evaluation engines.
    """
    
    @property
    @abstractmethod
    def mode_name(self) -> str:
        """Returns the name of the grading mode (e.g., 'simple', 'semantic', 'ai')"""
        pass
        
    @abstractmethod
    def grade(self, question_data: dict, user_answer: str) -> GradingResult:
        """
        Evaluates a user's answer against the expected answer and returns a standardized GradingResult.
        
        :param question_data: The dictionary containing 'answer', 'evaluation', 'marks', etc.
        :param user_answer: The normalized text submitted by the user.
        :return: A GradingResult dictionary.
        """
        pass
