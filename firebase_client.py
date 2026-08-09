import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()

FIREBASE_ENABLED = os.getenv("FIREBASE_ENABLED", "false").strip().lower() == "true"
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
FIREBASE_SERVICE_ACCOUNT_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
FIREBASE_COLLECTION = os.getenv("FIREBASE_COLLECTION", "ai_responses").strip()

_firestore_client: Optional[firestore.Client] = None


def _initialize_firestore() -> Optional[firestore.Client]:
    global _firestore_client

    if not FIREBASE_ENABLED:
        return None

    if _firestore_client is not None:
        return _firestore_client

    app_options = {}
    if FIREBASE_PROJECT_ID:
        app_options["projectId"] = FIREBASE_PROJECT_ID

    try:
        if FIREBASE_CREDENTIALS_JSON:
            credentials_data = json.loads(FIREBASE_CREDENTIALS_JSON)
            cred = credentials.Certificate(credentials_data)
            firebase_admin.initialize_app(cred, app_options or None)
        elif FIREBASE_SERVICE_ACCOUNT_PATH:
            service_account_path = Path(FIREBASE_SERVICE_ACCOUNT_PATH)
            if not service_account_path.is_absolute():
                service_account_path = Path(__file__).resolve().parent / service_account_path

            cred = credentials.Certificate(str(service_account_path))
            firebase_admin.initialize_app(cred, app_options or None)
        else:
            # Use application default credentials if available
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, app_options or None)

        _firestore_client = firestore.client()
        return _firestore_client
    except Exception as exc:
        logging.error(f"[Firebase] Failed to initialize Firestore: %s", exc)
        return None


def get_firestore_client() -> Optional[firestore.Client]:
    client = _firestore_client or _initialize_firestore()
    if FIREBASE_ENABLED and client is None:
        logging.error("[Firebase] FIREBASE_ENABLED=true but Firestore client could not be initialized")
    return client


def save_ai_response(collection_name: str, payload: Dict[str, Any]) -> None:
    if not FIREBASE_ENABLED:
        return

    client = get_firestore_client()
    if client is None:
        logging.error("[Firebase] Firestore client unavailable while attempting to save response")
        raise RuntimeError("Firestore client unavailable")

    try:
        record = {
            "created_at": datetime.utcnow().isoformat() + "Z",
            **payload
        }
        client.collection(collection_name or FIREBASE_COLLECTION).add(record)
    except Exception as exc:
        logging.error(f"[Firebase] Failed to save AI response: %s", exc)
        raise
