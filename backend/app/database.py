from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from app.config import MONGO_DB_NAME, MONGO_URI


_db = None


def get_database():
    """Get MongoDB database connection."""
    global _db
    if _db is None:
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            client.admin.command("ping")
            _db = client[MONGO_DB_NAME]
            print(f"Connected to MongoDB: {MONGO_DB_NAME}")
        except ServerSelectionTimeoutError as exc:
            print(f"Failed to connect to MongoDB at {MONGO_URI}")
            raise exc
    return _db


def close_database():
    """Close MongoDB connection."""
    global _db
    if _db is not None:
        _db.client.close()
        _db = None
