import json
import logging
from typing import Optional
import time

from .base_grader import BaseGrader, GradingResult
from config import NVIDIA_API_KEY, QA_AI_MODEL, QA_AI_FALLBACK_MODEL, QA_AI_TIMEOUT, QA_AI_TEMPERATURE
from prompts.qa_grading_prompt import QA_GRADING_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

class AIGrader(BaseGrader):
    @property
    def mode_name(self) -> str:
        return "ai"
        
    def _call_llm(self, prompt: str, model: str) -> dict:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package is not installed")
            
        if not NVIDIA_API_KEY:
            raise ValueError("NVIDIA_API_KEY is not set")
            
        client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY
        )
        
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=QA_AI_TEMPERATURE,
            top_p=0.95,
            max_tokens=2048,
            extra_body={"chat_template_kwargs": {"enable_thinking": True}, "reasoning_budget": 16384},
            stream=False,
            timeout=QA_AI_TIMEOUT
        )
        
        # The prompt strictly asks for JSON output
        content = completion.choices[0].message.content
        
        # Clean up markdown code blocks if the model ignored our instruction
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
            
        return json.loads(content.strip())

    def grade(self, question_data: dict, user_answer: str) -> GradingResult:
        expected_answer = question_data.get("answer", "")
        eval_data = question_data.get("evaluation", {})
        must_have = eval_data.get("must_have_keywords", [])
        max_marks = question_data.get("marks", 1)
        
        prompt = QA_GRADING_PROMPT_TEMPLATE.format(
            expected_answer=expected_answer,
            must_have_keywords=", ".join(must_have) if must_have else "None",
            max_marks=max_marks,
            user_answer=user_answer
        )
        
        # Retry logic: Iterate through models
        models_to_try = [
            QA_AI_MODEL,
            QA_AI_FALLBACK_MODEL,
            "bytedance/seed-oss-36b-instruct",
            "meta/llama-3.2-3b-instruct",
            "google/gemma-4-31b-it",
            "meta/llama-3.2-1b-instruct",
            "meta/llama-3.1-8b-instruct",
            "minimaxai/minimax-m3",
            "mistralai/mistral-small-4-119b-2603",
            "nvidia/nemotron-3-super-120b-a12b",
            "qwen/qwen3.5-122b-a10b"
        ]
        
        max_attempts = len(models_to_try)
        last_error = None
        failed_models = []
        
        for attempt, current_model in enumerate(models_to_try):
            try:
                result_json = self._call_llm(prompt, current_model)
                
                # Default similarity to percentage / 100 for AI
                percentage = result_json.get("percentage", 0)
                sim_score = percentage / 100.0
                
                # Derive keyword coverage
                missing_kws = result_json.get("missing_keywords", [])
                total_kws = len(must_have)
                kw_coverage = 1.0 if total_kws == 0 else max(0.0, (total_kws - len(missing_kws)) / total_kws)
                
                evaluator_str = f"NVIDIA AI ({current_model})"
                if failed_models:
                    evaluator_str += f" | Failed: {', '.join(failed_models)}"
                    
                return GradingResult(
                    marksAwarded=float(result_json.get("marks_awarded", 0)),
                    maxMarks=float(result_json.get("max_marks", max_marks)),
                    similarity=float(sim_score),
                    keywordCoverage=float(kw_coverage),
                    feedback=result_json.get("feedback", ""),
                    missingKeywords=missing_kws,
                    strengths=result_json.get("strengths", []),
                    overall=result_json.get("overall", ""),
                    evaluator=evaluator_str
                )
            except Exception as e:
                last_error = e
                error_msg = str(e)
                logger.warning(f"AI evaluation attempt {attempt + 1} with model {current_model} failed: {error_msg}")
                failed_models.append(current_model)
                
                if attempt < max_attempts - 1:
                    time.sleep(1) # Brief pause before retry
                    
        # If we exhausted retries
        raise RuntimeError(f"AI evaluation completely failed after {max_attempts} attempts. Last error: {last_error}. Failed models: {', '.join(failed_models)}")
