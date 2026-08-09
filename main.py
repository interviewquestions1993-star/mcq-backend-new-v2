import json
import random
import os
import logging
import traceback
import urllib.request
import urllib.parse
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Any, Dict

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import difflib
import httpx
from datetime import datetime

from services.grading.grading_service import GradingService
from services.email.email_service import send_quiz_results_email

# We have removed heavy AI models (torch, sentence-transformers, openai)
# due to memory constraints on the server.
SentenceTransformer = None
util = None
sentence_model = None

def ensure_sentence_model():
    pass

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_API_KEY,
    OLLAMA_MODEL,
    CHROMA_PERSIST_DIR,
    CHROMA_COLLECTION_NAME,
    FIRESTORE_HISTORY_COLLECTION,
    GOOGLE_OAUTH_CLIENT_ID,
    QA_GRADING_MODE,
    QA_BATCH_MAX_CONCURRENT,
)

# Firebase client (optional)
try:
    from .firebase_client import get_firestore_client, save_ai_response, FIREBASE_ENABLED, FIREBASE_COLLECTION
    from firebase_admin import firestore as _fb_firestore
except Exception:
    try:
        from firebase_client import get_firestore_client, save_ai_response, FIREBASE_ENABLED, FIREBASE_COLLECTION
        from firebase_admin import firestore as _fb_firestore
    except Exception as _e:
        logging.warning("Firebase client unavailable: %s", _e)
        get_firestore_client = None
        save_ai_response = None
        FIREBASE_ENABLED = False
        FIREBASE_COLLECTION = 'ai_responses'
        _fb_firestore = None

try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
except ImportError:
    id_token = None
    google_requests = None
    logging.warning('google-auth library unavailable: user token verification disabled')


# Custom exception for chapter not found
class ChapterNotFound(Exception):
    """Raised when a chapter's data file is not found on the remote source."""
    pass


app = FastAPI(title="NCERT Grade 8 Quiz Generator")

# CORS: allow local frontend during development plus GitHub Pages deployment
allowed_origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "https://interviewquestions1993-star.github.io",
]
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_origins=["https://interviewquestions1993-star.github.io"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================= CONFIG =========================
EMBEDDING_MODEL = "nomic-embed-text"
LLM_MODEL = OLLAMA_MODEL if ":" in OLLAMA_MODEL else f"{OLLAMA_MODEL}:latest"
PERSIST_DIR = CHROMA_PERSIST_DIR
COLLECTION_NAME = CHROMA_COLLECTION_NAME
# =======================================================

# Globals to be initialized lazily on first use
embeddings = None
vectorstore = None
client = None

# Configure basic logging to file for debugging server-side errors
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "backend.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)

# NOTE: Removed startup_event to allow fast server startup.
# Initialization happens lazily in ensure_initialized() on first use.


def ensure_initialized():
    """AI endpoints are disabled due to memory constraints."""
    global embeddings, vectorstore, client
    embeddings = None
    vectorstore = None
    client = None


class MCQRequest(BaseModel):
    topic: str
    num_questions: int = 5
    difficulty: Optional[str] = None
    source: Optional[str] = None


class CBSEMCQRequest(BaseModel):
    topic: Optional[str] = None
    num_questions: int = 10
    difficulty: Optional[str] = None
    chapter: Optional[str] = None
    version: Optional[str] = "V1"
    quiz_type: Optional[str] = "MCQ"
    board: Optional[str] = "CBSE"
    class_num: Optional[str] = Field(alias="class", default="8")
    subject: Optional[str] = "Science"

class ProgressRequest(BaseModel):
    board: str = "CBSE"
    class_num: str = Field(alias="class", default="8")
    subject: str = "Science"
    chapter: str
    version: str = "V1"
    questionType: str = "MCQ"


class MCQHistoryRecord(BaseModel):
    topic: str
    num_questions: int
    questions: list[dict]
    answers: dict
    score: int
    total: int
    percentage: int
    status: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None


class QAEvaluateRequest(BaseModel):
    questionId: int
    chapter: str
    version: str
    userAnswer: str

class QABatchAnswerItem(BaseModel):
    questionId: int
    userAnswer: str

class QABatchEvaluateRequest(BaseModel):
    examId: Optional[str] = None
    chapter: str
    version: str
    answers: list[QABatchAnswerItem]

class QAHistoryRecord(BaseModel):
    quizType: str = "qa"
    board: str = "CBSE"
    class_num: str = Field(alias="class", default="8")
    subject: str = "Science"
    chapter: str
    version: str
    questions: list[dict]
    totalMarks: float
    maximumMarks: float
    percentage: float
    completedAt: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None


RAW_CBSE_MCQS_BASE = "https://raw.githubusercontent.com/learnenglishandgrow93-web/cbse-mcq-bank/refs/heads/main/"
_CBSE_MCQS_CACHE = {}


def fetch_cbse_mcqs(chapter_name: Optional[str] = None, version: str = "V1", quiz_type: str = "MCQ"):
    """Fetch CBSE MCQs or QA from GitHub raw. If chapter_name is provided, construct
    the raw URL for that chapter file and fetch it. Caches per-URL results.
    Raises ChapterNotFound if chapter-specific URL returns 404.
    """
    global _CBSE_MCQS_CACHE
    # Build target URL
    if chapter_name:
        name = chapter_name.strip()
        filename = f"{name}-{quiz_type.upper()}-{version.lower()}"
        encoded = urllib.parse.quote(filename, safe='')
        url = RAW_CBSE_MCQS_BASE + encoded
        logging.info(f"Constructed chapter-specific URL: {url}")
        is_chapter_specific = True
    else:
        # Fallback to a default filename previously used
        url = RAW_CBSE_MCQS_BASE + "The%20Invisible%20Living%20World%3A%20Beyond%20Our%20Naked%20Eye"
        logging.info(f"Using default fallback URL: {url}")
        is_chapter_specific = False

    if url in _CBSE_MCQS_CACHE:
        return _CBSE_MCQS_CACHE[url]

    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            content = response.read().decode("utf-8")
            
            # Extract only the valid JSON array (from first [ to last ])
            # This handles files with extra text before or after the JSON
            first_bracket = content.find('[')
            last_bracket = content.rfind(']')
            if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
                content = content[first_bracket:last_bracket+1]
            
            # Some upstream files contain literal control characters (unescaped newlines,
            # tabs, or carriage returns) inside JSON string values which makes
            # `json.loads` fail. Sanitize the text by escaping control characters
            # that appear while inside a JSON string.
            def _sanitize_json_text(s: str) -> str:
                import re
                # Strip trailing commas from arrays and objects
                s = re.sub(r',\s*}', '}', s)
                s = re.sub(r',\s*]', ']', s)
                
                out_chars = []
                in_str = False
                esc = False
                for ch in s:
                    if ch == '"' and not esc:
                        in_str = not in_str
                        out_chars.append(ch)
                        esc = False
                        continue
                    if ch == '\\' and not esc:
                        esc = True
                        out_chars.append(ch)
                        continue
                    if esc:
                        # previous was a backslash, this char is escaped
                        out_chars.append(ch)
                        esc = False
                        continue
                    if in_str and ch == '\n':
                        out_chars.append('\\n')
                        continue
                    if in_str and ch == '\r':
                        out_chars.append('\\r')
                        continue
                    if in_str and ch == '\t':
                        out_chars.append('\\t')
                        continue
                    out_chars.append(ch)
                return ''.join(out_chars)

            sanitized = _sanitize_json_text(content)
            data = json.loads(sanitized)
    except urllib.error.HTTPError as exc:
        # If chapter-specific URL returns 404, raise ChapterNotFound
        if is_chapter_specific and exc.code == 404:
            raise ChapterNotFound(f"Chapter data not yet available: {chapter_name}") from exc
        raise RuntimeError(f"Failed to load CBSE MCQs from remote source ({url}): {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to load CBSE MCQs from remote source ({url}): {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("CBSE MCQ source must be a JSON array")

    _CBSE_MCQS_CACHE[url] = data
    return data


def convert_cbse_item(item: dict):
    # Normalize options - remove "A) ", "B) ", etc. prefix if present
    options = item.get("options", [])
    clean_options = []
    for opt in options:
        opt_str = str(opt).strip()
        # Remove "A) ", "B) ", "C) ", "D) " prefix
        clean_opt = re.sub(r'^[A-D][\)\.\:]\s*', '', opt_str)
        clean_options.append(clean_opt)
    
    # Normalize the correct answer to uppercase letter (A, B, C, D)
    raw_answer = item.get("answer") or item.get("correct_answer") or ""
    normalized_answer = ""
    if raw_answer:
        raw_str = str(raw_answer).strip()
        # Check if it's already a single letter A-D
        upper_str = raw_str.upper()
        if len(upper_str) == 1 and upper_str in ['A', 'B', 'C', 'D']:
            normalized_answer = upper_str
        else:
            # Answer is text (e.g., "Bacteria") - find matching option
            normalized_raw = raw_str.lower()
            for i, opt in enumerate(clean_options):
                if opt.lower() == normalized_raw:
                    normalized_answer = chr(ord('A') + i)
                    break
            # If no exact match, try partial match
            if not normalized_answer:
                for i, opt in enumerate(clean_options):
                    if normalized_raw in opt.lower() or opt.lower() in normalized_raw:
                        normalized_answer = chr(ord('A') + i)
                        break
            # Fallback to original if no match found
            if not normalized_answer:
                normalized_answer = raw_str
    
    return {
        "id": item.get("id"),
        "question": item.get("question", ""),
        "options": clean_options,
        "correct_answer": normalized_answer,
        "explanation": item.get("explanation", ""),
        "difficulty": str(item.get("difficulty", "")).capitalize() or "Medium",
    }


def verify_google_token(authorization: str | None = Header(None, alias="Authorization")):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must use Bearer scheme")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authorization token missing")

    if id_token is None or google_requests is None:
        raise HTTPException(status_code=500, detail="Server auth helper unavailable")

    try:
        request = google_requests.Request()
        # Allow tokens up to 24 hours (86400s) expired to prevent 401s during long quizzes
        claims = id_token.verify_oauth2_token(
            token, 
            request, 
            GOOGLE_OAUTH_CLIENT_ID or None,
            clock_skew_in_seconds=86400
        )
    except Exception as exc:
        logging.warning('Google token verification failed: %s', exc)
        raise HTTPException(status_code=401, detail="Invalid authentication token") from exc

    if not claims.get('sub'):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return {
        'user_id': claims.get('sub'),
        'user_email': claims.get('email'),
        'user_name': claims.get('name') or claims.get('email') or 'Unknown'
    }

def verify_google_token_optional(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    try:
        return verify_google_token(authorization)
    except Exception:
        return None

def extract_json(text: str):
    import re
    import json as json_module
    
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM response body was empty; no JSON could be extracted")

    # Remove markdown code fences
    code_fence_pattern = r'```(?:json|python|javascript|yaml)?\s*\n?(.*?)\n?```'
    match = re.search(code_fence_pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        text = match.group(1).strip()
    
    text = text.strip()
    if text.startswith('```'):
        text = text[3:]
    if text.endswith('```'):
        text = text[:-3]
    
    text = re.sub(r'^(json|python|javascript|yaml)\s*', '', text, flags=re.IGNORECASE).strip()
    
    # Find the first JSON structure
    start_positions = [pos for pos in (text.find('{'), text.find('[')) if pos != -1]
    if not start_positions:
        raise ValueError(f"Unable to locate JSON in LLM response: {text!r}")

    json_text = text[min(start_positions):]
    
    # Try standard JSON parsing first
    try:
        return json_module.loads(json_text)
    except json_module.JSONDecodeError:
        pass
    
    # Fix common LLM error: unquoted MCQ options like: B) text instead of "B) text"
    # This regex handles: , B) text, C) text patterns
    # We look for: comma + optional whitespace + letter + ) and add quotes around the option
    
    # Pattern explanation:
    # ,(\s+)([A-Z]\))(\s+) means: comma, spaces, letter), spaces
    # Replace with: ,"$2$3$quoted_option"
    
    def add_quotes_to_unquoted_options(s):
        """Add quotes around unquoted MCQ options in arrays."""
        # Find all positions where we have ", B)" pattern (comma, space, letter, paren)
        # and the content after isn't already quoted
        result = []
        lines = s.split('\n')
        for line in lines:
            # Look for "options": [ ... ] lines
            if '"options"' in line:
                # Find the array content
                match = re.search(r'"options"\s*:\s*\[(.*)\]', line)
                if match:
                    opts_content = match.group(1)
                    # Split by comma, but preserve quoted strings
                    # This is a simplification - just wrap unquoted items
                    opts_content = re.sub(
                        r',(\s*)([A-Z]\))',  # comma, optional space, letter, paren
                        r', "\2',             # replace with comma, space, quote, letter, paren
                        opts_content
                    )
                    # Now we need to close the quotes - find where each option ends
                    # Option ends before next comma (outside quotes) or at ]
                    opts_content = re.sub(
                        r'("\w\)[^"]*?)(?=,|$)',  # quoted option content, lookahead for comma or end
                        r'\1"',                    # close the quote
                        opts_content
                    )
                    line = f'"options": [{opts_content}]'
                result.append(line)
            else:
                result.append(line)
        return '\n'.join(result)
    
    json_text = add_quotes_to_unquoted_options(json_text)
    
    try:
        return json_module.loads(json_text)
    except json_module.JSONDecodeError as exc:
        raise ValueError(
            f"Could not parse JSON from LLM response. Tried standard JSON, manual fix attempts. Raw: {text[:300]!r}. Error: {exc}"
        ) from exc


def get_retriever(query: str):
    ensure_initialized()
    if vectorstore is None:
        raise RuntimeError("Vectorstore is not initialized")

    if not hasattr(vectorstore, "as_retriever"):
        raise RuntimeError("Chroma vectorstore has no as_retriever() method")

    retriever = vectorstore.as_retriever(search_kwargs={"k": 12})
    if retriever is None:
        raise RuntimeError("as_retriever() returned None")

    if hasattr(retriever, "get_relevant_documents"):
        docs = retriever.get_relevant_documents(query)
    elif hasattr(retriever, "retrieve"):
        docs = retriever.retrieve(query)
    elif hasattr(retriever, "invoke"):
        docs = retriever.invoke(query)
    else:
        raise RuntimeError("Retriever does not support get_relevant_documents, retrieve, or invoke")

    return docs


@app.post("/api/mcq/generate")
def generate_mcqs(request: MCQRequest):
    try:
        query = f"NCERT Grade 8 {request.topic}" if request.topic else "NCERT Grade 8"
        docs = get_retriever(query)
        if not docs:
            logging.warning("Retriever returned no documents for query: %s", query)
            context = (
                "No reference documents were found in the vector store. "
                "Generate the MCQs using NCERT Grade 8 knowledge only."
            )
        else:
            context = "\n\n".join([doc.page_content for doc in docs])

        system_prompt = f"""You are an expert CBSE NCERT Grade 8 teacher with 15+ years of experience.
Your task is to generate exactly {request.num_questions} fresh, high-quality MCQs for the topic '{request.topic}'.
Use ONLY the provided context and NCERT-aligned concepts.

Return a valid JSON object with the following structure:
{{
  "topic": "{request.topic}",
  "num_questions": {request.num_questions},
  "questions": [
    {{
      "id": 1,
      "question": "...",
      "options": ["A) ...", "B) ...", "C) ...", "D) ..."],
      "correct_answer": "A",
      "explanation": "...",
      "difficulty": "Easy|Medium|Hard"
    }}
  ],
  "status": "success"
}}
Only return valid JSON. Do not include any extra text outside the JSON object."""

        if request.difficulty:
            system_prompt += f"\nUse the requested difficulty level: {request.difficulty}."
        if request.source:
            system_prompt += f"\nUse information from this source if relevant: {request.source}."

        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n\n{context}\n\nGenerate the JSON payload now."}
            ],
            temperature=0.82,
            max_tokens=3500,
            top_p=0.92
        )

        choice = response.choices[0]
        content = None

        # OpenAI wrapper may expose the assistant text in different fields
        if hasattr(choice, 'message') and getattr(choice, 'message', None) is not None:
            content = getattr(choice.message, 'content', None)
        if not content and hasattr(choice, 'text'):
            content = getattr(choice, 'text', None)
        if not content and hasattr(choice, 'content'):
            content = getattr(choice, 'content', None)

        logging.info('LLM raw choice content: %r', content)
        if not content:
            raw = None
            try:
                raw = choice.to_dict() if hasattr(choice, 'to_dict') else repr(choice)
            except Exception:
                raw = repr(choice)
            raise RuntimeError(f"LLM response content was empty. choice={raw}")

        data = extract_json(content)

        return data

    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        logging.error("Exception in /api/mcq/generate:\n%s", tb)
        with open(os.path.join(LOG_DIR, "last_error.txt"), "w", encoding="utf-8") as fh:
            fh.write(tb)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/progress")
def get_progress(
    board: str = Query("CBSE"),
    class_num: str = Query("8", alias="class"),
    subject: str = Query("Science"),
    chapter: str = Query(...),
    version: str = Query("V1"),
    questionType: str = Query("MCQ"),
    current_user: dict = Depends(verify_google_token)
):
    import re
    try:
        entries = fetch_cbse_mcqs(chapter_name=chapter, version=version, quiz_type=questionType)
        total = len(entries)
    except Exception:
        total = 0

    attempted_ids = []
    last_attempt = None
    if get_firestore_client and FIREBASE_ENABLED:
        try:
            db = get_firestore_client()
            doc_id = f"{board}_{class_num}_{subject}_{chapter}_{questionType}_{version}"
            doc_id = re.sub(r'[^a-zA-Z0-9_]', '_', doc_id.replace(' ', '_')).lower()
            doc_snap = db.collection("users").document(current_user["user_id"]).collection("progress").document(doc_id).get()
            if doc_snap.exists:
                data = doc_snap.to_dict()
                attempted_ids = data.get("attemptedQuestionIds", [])
                last_attempt = data.get("lastAttemptAt")
        except Exception as exc:
            logging.error("Failed to get progress: %s", exc)

    attempted = len(attempted_ids)
    remaining = max(0, total - attempted)
    percentage = (attempted / total * 100) if total > 0 else 0.0
    completed = (attempted >= total) and total > 0

    return {
        "board": board,
        "class": class_num,
        "subject": subject,
        "chapter": chapter,
        "version": version,
        "questionType": questionType,
        "totalQuestions": total,
        "attemptedQuestions": attempted,
        "remainingQuestions": remaining,
        "percentageCompleted": percentage,
        "completed": completed,
        "lastAttemptAt": last_attempt
    }

@app.post("/api/progress/reset")
def reset_progress(
    request: ProgressRequest,
    current_user: dict = Depends(verify_google_token)
):
    import re
    if get_firestore_client and FIREBASE_ENABLED:
        try:
            db = get_firestore_client()
            doc_id = f"{request.board}_{request.class_num}_{request.subject}_{request.chapter}_{request.questionType}_{request.version}"
            doc_id = re.sub(r'[^a-zA-Z0-9_]', '_', doc_id.replace(' ', '_')).lower()
            db.collection("users").document(current_user["user_id"]).collection("progress").document(doc_id).delete()
        except Exception as exc:
            logging.error("Failed to reset progress: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to reset progress")

    return {"status": "success", "message": "Progress reset"}

@app.post("/api/mcq/cbse")
def get_cbse_mcqs(request: CBSEMCQRequest, current_user: dict | None = Depends(verify_google_token_optional)):
    import re

    raw_query = (request.topic or "").strip()
    query = raw_query.lower()
    difficulty = (request.difficulty or "").strip().lower()

    # Attempt to fetch chapter-specific MCQs based on the topic
    entries = None
    chapter_not_found = False
    chapter_specific_fetch = False
    
    if request.chapter:
        chapter_name = request.chapter
        version = request.version or "V1"
        quiz_type = request.quiz_type or "MCQ"
    else:
        chapter_name = None
        version = "V1"
        quiz_type = "MCQ"
        if raw_query:
            if ":" in raw_query:
                # Keep everything after the first colon; chapter titles may contain colons
                chapter_name = raw_query.split(":", 1)[1].strip()
            elif "-" in raw_query:
                parts = [p.strip() for p in raw_query.split("-") if p.strip()]
                if parts:
                    chapter_name = parts[-1]
            else:
                # If the topic looks like a chapter title (multiple words), use it
                if len(raw_query.split()) > 2:
                    chapter_name = raw_query
            
            if chapter_name and re.search(r'-(MCQ|QA)-(v\d+)$', chapter_name, re.IGNORECASE):
                match = re.search(r'-(MCQ|QA)-(v\d+)$', chapter_name, re.IGNORECASE)
                quiz_type = match.group(1).upper()
                version = match.group(2).upper()
                chapter_name = chapter_name[:match.start()].strip()
            elif chapter_name and re.search(r'-v\d+$', chapter_name, re.IGNORECASE):
                match = re.search(r'-(v\d+)$', chapter_name, re.IGNORECASE)
                version = match.group(1).upper()
                chapter_name = chapter_name[:match.start()].strip()


    if chapter_name:
        try:
            entries = fetch_cbse_mcqs(chapter_name=chapter_name, version=version, quiz_type=quiz_type)
            chapter_specific_fetch = True
        except ChapterNotFound as exc:
            # Chapter data not yet available
            logging.warning("Chapter not found: %s", exc)
            chapter_not_found = True
        except Exception as exc:
            logging.warning("Failed to fetch CBSE MCQs for chapter '%s': %s", chapter_name, exc)

    # If chapter was not found, return a message to the user
    if chapter_not_found:
        return {
            "topic": request.topic or "CBSE",
            "num_questions": 0,
            "questions": [],
            "status": "chapter_not_available",
            "message": f"Chapter '{chapter_name}' is not yet available. Please check back soon!",
        }

    # Fallback to default source if chapter-specific fetch failed or was not attempted
    if entries is None:
        try:
            entries = fetch_cbse_mcqs()
        except Exception as exc:
            logging.error("CBSE MCQ fetch failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    # Build a set of candidate query variants to improve matching for verbose
    # topics like "CBSE Class 8 science: The Invisible Living World: Beyond Our Naked Eye"
    queries = set()
    queries.add(query)

    # If the topic contains colons (:) or hyphens, add the last segment as a focused query
    if ":" in raw_query:
        for seg in raw_query.split(":"):
            s = seg.strip().lower()
            if s:
                queries.add(s)
    if "-" in raw_query:
        for seg in raw_query.split("-"):
            s = seg.strip().lower()
            if s:
                queries.add(s)

    # Strip common prefixes like 'cbse' and 'class <num>' to get the core topic
    q_clean = re.sub(r"\bcbse\b", "", query)
    q_clean = re.sub(r"\bclass\s*\d+\b", "", q_clean)
    q_clean = re.sub(r"[^a-z0-9\s]", " ", q_clean)
    q_clean = re.sub(r"\s+", " ", q_clean).strip()
    if q_clean:
        queries.add(q_clean)

    # Also add single-word tokens from the cleaned query as loose matches (avoid very short tokens)
    for token in q_clean.split():
        if len(token) > 3:
            queries.add(token)

    filtered = []
    for item in entries:
        subject = str(item.get("subject", "")).lower()
        chapter = str(item.get("chapter", "")).lower()
        question_text = str(item.get("question", "")).lower()
        item_difficulty = str(item.get("difficulty", "")).lower()

        # Check if any candidate query variant appears in any of the searchable fields
        matched = False
        for q in queries:
            if not q:
                continue
            if q in subject or q in chapter or q in question_text:
                matched = True
                break

        if query and not matched:
            # no candidate matched this item
            continue
        if difficulty and difficulty != item_difficulty:
            continue
        filtered.append(item)

    # If still nothing found but a topic was provided, try a relaxed filter: check whether
    # all non-trivial words from the cleaned query appear somewhere in the item fields.
    if not filtered and raw_query:
        tokens = [t for t in q_clean.split() if len(t) > 3]
        if tokens:
            for item in entries:
                subject = str(item.get("subject", "")).lower()
                chapter = str(item.get("chapter", "")).lower()
                question_text = str(item.get("question", "")).lower()
                hay = " ".join([subject, chapter, question_text])
                if all(tok in hay for tok in tokens):
                    filtered.append(item)

    # If topic-specific filtering returns no items but the chapter-specific source was loaded,
    # fallback to any item from that chapter to avoid false negatives on exact matching.
    if not filtered and chapter_specific_fetch and raw_query:
        for item in entries:
            chapter = str(item.get("chapter", "")).lower()
            if q_clean and q_clean in chapter:
                filtered.append(item)

    if not filtered and request.topic:
        raise HTTPException(status_code=404, detail="No matching CBSE MCQs were found for this topic.")

    # If no items matched and no topic was provided, use the full entries pool
    if not filtered:
        pool = entries.copy()
    else:
        pool = filtered

    # --- Progress Tracking Logic ---
    if current_user and get_firestore_client and FIREBASE_ENABLED and request.board and request.subject and chapter_name:
        try:
            db = get_firestore_client()
            doc_id = f"{request.board}_{request.class_num}_{request.subject}_{chapter_name}_{quiz_type.upper()}_{version.upper()}"
            # Normalize doc id: replace spaces with underscores, remove special chars
            doc_id = re.sub(r'[^a-zA-Z0-9_]', '_', doc_id.replace(' ', '_')).lower()
            
            progress_ref = db.collection("users").document(current_user["user_id"]).collection("progress").document(doc_id)
            doc_snap = progress_ref.get()
            if doc_snap.exists:
                data = doc_snap.to_dict()
                attempted = data.get("attemptedQuestionIds", [])
                
                if attempted:
                    pool = [q for q in pool if q.get("id") not in attempted and q.get("questionId") not in attempted]
        except Exception as exc:
            logging.error("Failed to fetch progress for filtering: %s", exc)

    if len(pool) == 0 and len(entries) > 0:
        return {
            "topic": request.topic or "CBSE",
            "num_questions": 0,
            "questions": [],
            "status": "completed",
            "completed": True,
            "message": "Congratulations! You have attempted all questions for this chapter. Click Reset Progress to practice again."
        }

    # Randomize selection so each call returns a different subset
    if request.num_questions < 0:
        count = len(pool)
    else:
        count = max(1, min(request.num_questions, len(pool)))
    random.shuffle(pool)
    selected = pool[:count]
    
    if quiz_type and quiz_type.upper() == "QA":
        questions = []
        for item in selected:
            safe_item = item.copy()
            safe_item.pop("answer", None)
            safe_item.pop("correct_answer", None)
            safe_item.pop("explanation", None)
            safe_item.pop("evaluation", None)
            safe_item.pop("keywords", None)
            questions.append(safe_item)
    else:
        questions = [convert_cbse_item(item) for item in selected]

    return {
        "topic": request.topic or "CBSE",
        "num_questions": len(questions),
        "questions": questions,
        "status": "success",
    }


@app.post("/api/mcq/history")
def save_mcq_history(record: MCQHistoryRecord, current_user: dict | None = Depends(verify_google_token)):
    if current_user:
        record.user_id = current_user.get('user_id')
        record.user_email = current_user.get('user_email')
        record.user_name = current_user.get('user_name')

    collection_name = FIRESTORE_HISTORY_COLLECTION or FIREBASE_COLLECTION
    if not FIREBASE_ENABLED:
        logging.info("Firebase not enabled — history not persisted remotely")
        return {"status": "success", "message": "History received (not persisted - Firebase disabled).", "record": record.dict()}

    if save_ai_response is None:
        logging.error("Firebase persistence requested but save_ai_response is unavailable")
        raise HTTPException(status_code=500, detail="Firebase support is unavailable on the server")

    try:
        save_ai_response(collection_name, record.dict())
        
        # --- Progress Tracking ArrayUnion ---
        if current_user and get_firestore_client and record.topic:
            import re
            db = get_firestore_client()
            board, class_num, subject, chapter, quiz_type, version = "CBSE", "8", "Science", "", "MCQ", "V1"
            m = re.match(r"(?i)(CBSE|ICSE|NEET|JEE)\s+Class\s+(\d+)\s+([a-z0-9\- ]+):", record.topic)
            if m:
                board = m.group(1).upper()
                class_num = m.group(2)
                subject = m.group(3).strip().title()
                rest = record.topic[m.end():].strip()
                match = re.search(r'-(MCQ|QA)-(v\d+)$', rest, re.IGNORECASE)
                if match:
                    quiz_type = match.group(1).upper()
                    version = match.group(2).upper()
                    chapter = rest[:match.start()].strip()
            
            if chapter and _fb_firestore:
                doc_id = f"{board}_{class_num}_{subject}_{chapter}_{quiz_type}_{version}"
                doc_id = re.sub(r'[^a-zA-Z0-9_]', '_', doc_id.replace(' ', '_')).lower()
                
                attempted_ids = []
                for q in record.questions:
                    qid = q.get("id") or q.get("questionId")
                    if qid is not None:
                        attempted_ids.append(qid)
                        
                if attempted_ids:
                    progress_ref = db.collection("users").document(current_user["user_id"]).collection("progress").document(doc_id)
                    progress_ref.set({
                        "attemptedQuestionIds": _fb_firestore.ArrayUnion(attempted_ids),
                        "lastAttemptAt": datetime.utcnow().isoformat() + "Z"
                    }, merge=True)
                    
                    # Update totalAttempted length via a transaction or simple read (for simplicity, we let /api/progress calculate it on read)
        # ------------------------------------

        return {"status": "success", "message": "History saved to Firebase.", "record": record.dict()}
    except Exception as exc:
        logging.error("Failed to save history: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save history to Firebase") from exc


@app.get("/api/mcq/history")
def list_mcq_history(limit: int = 50, current_user: dict = Depends(verify_google_token)):
    # Return persisted quiz attempts for the authenticated user, most recent first
    if not FIREBASE_ENABLED:
        logging.info("Firebase history requested but FIREBASE_ENABLED is false")
        return []

    if get_firestore_client is None:
        logging.error("Firebase history requested but get_firestore_client is unavailable")
        raise HTTPException(status_code=500, detail="Firebase client is unavailable")

    client = get_firestore_client()
    if client is None:
        logging.error("Firebase history requested but Firestore client could not be initialized")
        raise HTTPException(status_code=500, detail="Firestore client is unavailable")

    try:
        coll = client.collection(FIRESTORE_HISTORY_COLLECTION or FIREBASE_COLLECTION)
        query = coll.order_by('created_at', direction=_fb_firestore.Query.DESCENDING).limit(limit)
        query = query.where('user_id', '==', current_user['user_id'])
        docs = query.stream()
        items = []
        for d in docs:
            data = d.to_dict()
            data['id'] = d.id
            items.append(data)
        return items
    except Exception as exc:
        logging.error("Failed to list persisted mcqs: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
def health():
    return {"status": "healthy", "message": "Ollama backend is ready"}

@app.post("/api/qa/submit-answer")
def submit_qa_answer(request: QAEvaluateRequest):
    try:
        entries = fetch_cbse_mcqs(chapter_name=request.chapter, version=request.version, quiz_type="QA")
        question_data = next((q for q in entries if q.get("id") == request.questionId), None)
        
        if not question_data:
            raise HTTPException(status_code=404, detail="Question not found")
            
        result = GradingService.evaluate_answer(
            question_id=str(request.questionId),
            chapter=request.chapter,
            version=request.version,
            question_data=question_data,
            user_answer=request.userAnswer,
            preferred_mode=QA_GRADING_MODE
        )
            
        # Match exactly the frontend API contract
        return {
            "marksAwarded": result["marksAwarded"],
            "maxMarks": result["maxMarks"],
            "similarity": result["similarity"],
            "keywordCoverage": result["keywordCoverage"],
            "feedback": result["feedback"],
            "missingKeywords": result["missingKeywords"],
            "evaluator": result.get("evaluator", "Unknown"),
            "modelAnswer": question_data.get("answer", ""),
            "explanation": question_data.get("explanation", ""),
            "page_number": question_data.get("page_number", "")
        }
    except Exception as e:
        logging.error(f"Error submitting QA answer: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/qa/evaluate-batch")
def evaluate_qa_batch(request: QABatchEvaluateRequest, current_user: dict | None = Depends(verify_google_token)):
    batch_id = str(uuid.uuid4())
    start_time = time.time()
    user_id = current_user.get('user_id') if current_user else "anonymous"
    
    try:
        entries = fetch_cbse_mcqs(chapter_name=request.chapter, version=request.version, quiz_type="QA")
    except Exception as e:
        logging.error(f"Batch {batch_id} failed to fetch questions: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch questions")
        
    results = []
    failed = []
    
    # Filter empty answers
    valid_answers = [ans for ans in request.answers if ans.userAnswer and ans.userAnswer.strip()]
    total_questions = len(valid_answers)
    
    if total_questions == 0:
        return {
            "success": True,
            "results": [],
            "failed": [],
            "summary": {"totalQuestions": 0, "evaluated": 0, "failed": 0}
        }
        
    def evaluate_single(ans: QABatchAnswerItem):
        q_start = time.time()
        try:
            question_data = next((q for q in entries if q.get("id") == ans.questionId), None)
            if not question_data:
                raise ValueError(f"Question ID {ans.questionId} not found")
                
            res = GradingService.evaluate_answer(
                question_id=str(ans.questionId),
                chapter=request.chapter,
                version=request.version,
                question_data=question_data,
                user_answer=ans.userAnswer,
                preferred_mode=QA_GRADING_MODE
            )
            
            result_obj = {
                "questionId": ans.questionId,
                "marksAwarded": res["marksAwarded"],
                "maxMarks": res["maxMarks"],
                "similarity": res["similarity"],
                "keywordCoverage": res["keywordCoverage"],
                "feedback": res["feedback"],
                "missingKeywords": res["missingKeywords"],
                "evaluator": res.get("evaluator", "Unknown"),
                "modelAnswer": question_data.get("answer", ""),
                "explanation": question_data.get("explanation", ""),
                "page_number": question_data.get("page_number", ""),
                "evaluationTimeMs": int((time.time() - q_start) * 1000)
            }
            
            # Incremental save
            if FIREBASE_ENABLED and save_ai_response:
                save_data = result_obj.copy()
                save_data["batchId"] = batch_id
                save_data["userId"] = user_id
                save_data["examId"] = request.examId
                save_ai_response("qa_individual_evaluations", save_data)
                
            return True, result_obj
        except Exception as e:
            return False, {"questionId": ans.questionId, "error": str(e)}

    # Controlled Concurrency
    ai_times = []
    with ThreadPoolExecutor(max_workers=QA_BATCH_MAX_CONCURRENT) as executor:
        futures = {executor.submit(evaluate_single, ans): ans for ans in valid_answers}
        for future in as_completed(futures):
            success, data = future.result()
            if success:
                results.append(data)
                ai_times.append(data.get("evaluationTimeMs", 0))
            else:
                failed.append(data)
                
    end_time = time.time()
    total_processing_time = end_time - start_time
    avg_ai_time = sum(ai_times) / len(ai_times) if ai_times else 0
    
    # Logging
    logging.info(
        f"[BATCH EVALUATION] BatchID: {batch_id} | UserID: {user_id} | ExamID: {request.examId} | "
        f"Total Questions: {total_questions} | Successful: {len(results)} | Failed: {len(failed)} | "
        f"Avg AI Time: {avg_ai_time:.0f}ms | Total Processing Time: {total_processing_time:.2f}s"
    )
    
    return {
        "success": True,
        "results": results,
        "failed": failed,
        "summary": {
            "totalQuestions": total_questions,
            "evaluated": len(results),
            "failed": len(failed)
        }
    }

@app.post("/api/qa/history")
def save_qa_history(record: QAHistoryRecord, current_user: dict | None = Depends(verify_google_token)):
    if current_user:
        record.user_id = current_user.get('user_id')
        record.user_email = current_user.get('user_email')
        record.user_name = current_user.get('user_name')

    collection_name = FIRESTORE_HISTORY_COLLECTION or FIREBASE_COLLECTION
    if not FIREBASE_ENABLED:
        logging.info("Firebase not enabled — history not persisted remotely")
        return {"status": "success", "message": "History received (not persisted - Firebase disabled).", "record": record.dict()}

    if save_ai_response is None:
        raise HTTPException(status_code=500, detail="Firebase support is unavailable on the server")

    try:
        save_ai_response(collection_name, record.dict())
        
        # --- Progress Tracking ArrayUnion ---
        if current_user and get_firestore_client and _fb_firestore and record.chapter:
            import re
            db = get_firestore_client()
            doc_id = f"{record.board}_{record.class_num}_{record.subject}_{record.chapter}_QA_{record.version}"
            doc_id = re.sub(r'[^a-zA-Z0-9_]', '_', doc_id.replace(' ', '_')).lower()
            
            attempted_ids = []
            for q in record.questions:
                qid = q.get("id") or q.get("questionId")
                if qid is not None:
                    attempted_ids.append(qid)
                    
            if attempted_ids:
                progress_ref = db.collection("users").document(current_user["user_id"]).collection("progress").document(doc_id)
                progress_ref.set({
                    "attemptedQuestionIds": _fb_firestore.ArrayUnion(attempted_ids),
                    "lastAttemptAt": datetime.utcnow().isoformat() + "Z"
                }, merge=True)
        # ------------------------------------
        
        return {"status": "success", "message": "History saved to Firebase.", "record": record.dict()}
    except Exception as exc:
        logging.error("Failed to save history: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save history to Firebase") from exc


class StudyRequest(BaseModel):
    topic: str
    num_questions: int = -1  # -1 = all questions


@app.post("/api/study/questions")
def get_study_questions(request: StudyRequest):
    """Return all QA questions WITH answers and keywords for Study Mode.
    Reuses the same fetch_cbse_mcqs infrastructure as the QA quiz endpoint."""
    raw_topic = request.topic or ""

    # Parse chapter name and version from topic string
    # Expected format: "CBSE Class X Subject: Chapter Name-QA-vN"
    chapter_name: Optional[str] = None
    version = "V1"

    # Extract everything after the FIRST colon so chapter titles containing
    # colons (e.g. "The Invisible Living World: Beyond Our Naked Eye") are preserved.
    if ":" in raw_topic:
        after_colon = raw_topic.split(":", 1)[1].strip()
    else:
        after_colon = raw_topic.strip()

    # Strip type suffix: "-QA-v1" or "-Study-v1" or "-MCQ-v1"
    match = re.search(r"-(MCQ|QA|Study)-(v\d+)$", after_colon, re.IGNORECASE)
    if match:
        version = match.group(2).upper()
        chapter_name = after_colon[:match.start()].strip()
    elif re.search(r"-v\d+$", after_colon, re.IGNORECASE):
        vm = re.search(r"-(v\d+)$", after_colon, re.IGNORECASE)
        version = vm.group(1).upper()
        chapter_name = after_colon[:vm.start()].strip()
    else:
        chapter_name = after_colon

    try:
        entries = fetch_cbse_mcqs(chapter_name=chapter_name, version=version, quiz_type="QA")
    except ChapterNotFound:
        return {
            "topic": raw_topic,
            "num_questions": 0,
            "questions": [],
            "status": "chapter_not_available",
            "message": f"Chapter '{chapter_name}' is not yet available.",
        }
    except Exception as exc:
        logging.error("Study fetch failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Filter to only descriptive questions
    pool = [q for q in entries if q.get("question_type", "").lower() == "descriptive" or q.get("answer")]

    if request.num_questions > 0:
        random.shuffle(pool)
        pool = pool[:request.num_questions]
    else:
        # Sort by id for consistent order in study mode
        pool = sorted(pool, key=lambda q: q.get("id", 0))

    # Return ALL fields – do not strip answer, keywords or explanation
    questions = []
    for item in pool:
        kw = item.get("keywords", [])
        if isinstance(kw, str):
            kw = [k.strip() for k in kw.split(",") if k.strip()]
        questions.append({
            "id": item.get("id"),
            "question": item.get("question", ""),
            "answer": item.get("answer", ""),
            "difficulty": str(item.get("difficulty", "")).capitalize() or "Medium",
            "marks": item.get("marks", 1),
            "expected_word_count": item.get("expected_word_count"),
            "keywords": kw,
            "explanation": item.get("explanation", ""),
            "page_number": item.get("page_number", ""),
            "question_type": item.get("question_type", "descriptive"),
        })

    return {
        "topic": raw_topic,
        "num_questions": len(questions),
        "questions": questions,
        "status": "success",
    }


class EmailResultsRequest(BaseModel):
    topic: str
    score: float
    total: int
    percentage: float
    type: str = "mcq"
    questions: list[dict]
    answers: dict

@app.post("/api/email/results")
def email_quiz_results(request: EmailResultsRequest, current_user: dict | None = Depends(verify_google_token_optional)):
    try:
        results_data = request.dict()
        send_quiz_results_email(results_data)
        return {"status": "success", "message": "Email sent successfully"}
    except Exception as e:
        logging.error(f"Error in /api/email/results: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    print(f"🚀 Starting NCERT Quiz Generator with model: {LLM_MODEL}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
