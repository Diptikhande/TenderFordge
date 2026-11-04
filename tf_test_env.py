"""
Test isolation for TenderForge.

Import this module BEFORE importing config / database / analyzer in any test.
It points TENDERFORGE_DATA_DIR at a fresh temporary directory (unless one is already set),
enables TENDERFORGE_TEST_MODE (config.py and database.py then refuse to open the production DB),
and copies the sample tender PDFs into the temporary uploads folder so tests can read them.
The production database is never opened by this module; only its checksum is read.
"""

import atexit
import hashlib
import os
import shutil
import tempfile

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTION_DB = os.path.join(PROJECT_DIR, "data", "tenderforge.db")
PRODUCTION_UPLOADS = os.path.join(PROJECT_DIR, "data", "uploads")


def file_md5(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


PRODUCTION_DB_MD5_AT_START = file_md5(PRODUCTION_DB)

if not os.environ.get("TENDERFORGE_DATA_DIR"):
    _tmp = tempfile.mkdtemp(prefix="tenderforge_test_")
    os.environ["TENDERFORGE_DATA_DIR"] = _tmp
    atexit.register(shutil.rmtree, _tmp, ignore_errors=True)
os.environ.pop("TENDERFORGE_DB_PATH", None)
os.environ["TENDERFORGE_TEST_MODE"] = "1"

TEST_DATA_DIR = os.environ["TENDERFORGE_DATA_DIR"]
TEST_UPLOAD_DIR = os.path.join(TEST_DATA_DIR, "uploads")
os.makedirs(TEST_UPLOAD_DIR, exist_ok=True)

if os.path.exists(PRODUCTION_DB):
    _test_db = os.path.join(TEST_DATA_DIR, "tenderforge.db")
    if not os.path.exists(_test_db):
        shutil.copyfile(PRODUCTION_DB, _test_db)

# Read-only copies of the sample tenders used by pipeline tests
if os.path.isdir(PRODUCTION_UPLOADS):
    for _name in os.listdir(PRODUCTION_UPLOADS):
        if _name.lower().endswith(".pdf"):
            _dst = os.path.join(TEST_UPLOAD_DIR, _name)
            if not os.path.exists(_dst):
                shutil.copyfile(os.path.join(PRODUCTION_UPLOADS, _name), _dst)


def sample_pdf(name: str) -> str:
    """Path of a sample tender inside the temporary uploads folder."""
    return os.path.join(TEST_UPLOAD_DIR, name)


def live_groq_enabled() -> bool:
    """Live Groq tests run only when explicitly requested (they consume API quota)."""
    return os.getenv("TENDERFORGE_LIVE_GROQ") == "1"


def assert_production_db_untouched():
    now = file_md5(PRODUCTION_DB)
    assert now == PRODUCTION_DB_MD5_AT_START, "Production database changed during the test run!"
