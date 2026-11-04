"""
TenderForge Configuration
Reads settings from environment variables. Never hardcode secrets.
"""

import os
from dotenv import load_dotenv

# --- Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

# The production data location. Tests must never point DB_PATH here.
PRODUCTION_DATA_DIR = os.path.join(BASE_DIR, "data")
PRODUCTION_DB_PATH = os.path.join(PRODUCTION_DATA_DIR, "tenderforge.db")

# TENDERFORGE_DATA_DIR / TENDERFORGE_DB_PATH allow tests (and deployments) to use another location.
DATA_DIR = os.path.abspath(os.getenv("TENDERFORGE_DATA_DIR") or PRODUCTION_DATA_DIR)
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.abspath(os.getenv("TENDERFORGE_DB_PATH") or os.path.join(DATA_DIR, "tenderforge.db"))

# Test mode refuses to run against the production database.
TEST_MODE = os.getenv("TENDERFORGE_TEST_MODE") == "1"
if TEST_MODE and os.path.normcase(DB_PATH) == os.path.normcase(os.path.abspath(PRODUCTION_DB_PATH)):
    raise RuntimeError(
        "TENDERFORGE_TEST_MODE is set but DB_PATH points to the production database. "
        "Set TENDERFORGE_DATA_DIR or TENDERFORGE_DB_PATH to a temporary location."
    )

# --- API Keys & Models (loaded from env) ---
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY", "") or "").strip()
GROQ_API_KEY = (os.getenv("GROQ_API_KEY", "") or "").strip()
GROQ_MODEL = (os.getenv("GROQ_MODEL") or "qwen/qwen3.8-27b").strip()
GROQ_FALLBACK_MODELS = ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
NOT_FOUND = "Not found in available evidence"

# Local-only embedding inference is independent from the Groq chat-completion provider.
EMBEDDING_MODEL = (os.getenv("TENDERFORGE_EMBEDDING_MODEL") or "sentence-transformers/all-MiniLM-L6-v2").strip()
EMBEDDING_DEVICE = (os.getenv("TENDERFORGE_EMBEDDING_DEVICE") or "cpu").strip()
RAG_CHUNK_SIZE = int(os.getenv("TENDERFORGE_RAG_CHUNK_SIZE", "1200"))
RAG_CHUNK_OVERLAP = int(os.getenv("TENDERFORGE_RAG_CHUNK_OVERLAP", "160"))

# --- App Settings ---
APP_NAME = "TenderForge"
APP_VERSION = "0.3.0"
MAX_UPLOAD_SIZE_MB = 50

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
