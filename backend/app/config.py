import os
from dotenv import load_dotenv

load_dotenv()

# API Configuration
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "tenderforge")

# CORS Configuration
CORS_ORIGINS = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:3000",  # Alternative frontend port
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

# Dataset Configuration
DATASET_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "dataset", "tenders")
)
