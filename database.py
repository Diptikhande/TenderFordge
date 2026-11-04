"""
TenderForge Database Module
SQLite operations for multi-tenant user authentication, company profiles, and tender metadata.
"""

import sqlite3
import hashlib
import hmac
import secrets
import json
import os
import re
import uuid
from config import DATA_DIR, DB_PATH, PRODUCTION_DB_PATH


COMPANY_EVIDENCE_STATUSES = ("unverified", "verified", "rejected")


def get_connection():
    """Get a SQLite connection with row factory."""
    # Defence in depth: test runs must never open the production database.
    if os.getenv("TENDERFORGE_TEST_MODE") == "1" and \
            os.path.normcase(os.path.abspath(DB_PATH)) == os.path.normcase(os.path.abspath(PRODUCTION_DB_PATH)):
        raise RuntimeError("Refusing to open the production database while TENDERFORGE_TEST_MODE=1.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist and run safe migrations."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Company Profile Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            company_name TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            years_of_experience TEXT DEFAULT '',
            annual_turnover TEXT DEFAULT '',
            certifications TEXT DEFAULT '',
            technical_capabilities TEXT DEFAULT '',
            past_projects TEXT DEFAULT '',
            financial_details TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            contact_email TEXT DEFAULT '',
            contact_phone TEXT DEFAULT '',
            contact_address TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. Tenders Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            extracted_text TEXT DEFAULT '',
            page_count INTEGER DEFAULT 0,
            upload_status TEXT DEFAULT 'success',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 4. Tender Analyses Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            tender_id INTEGER NOT NULL,
            profile_hash TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            executive_summary TEXT DEFAULT '',
            structured_tender_data TEXT DEFAULT '{}',
            compliance_matrix TEXT DEFAULT '[]',
            risks TEXT DEFAULT '[]',
            missing_information TEXT DEFAULT '[]',
            model_used TEXT DEFAULT '',
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tender_id) REFERENCES tenders (id) ON DELETE CASCADE
        )
    """)

    # 5. User-Curated Tender Requirements (manual additions / corrections)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tender_id INTEGER NOT NULL,
            requirements TEXT DEFAULT '[]',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tender_id, user_id),
            FOREIGN KEY (tender_id) REFERENCES tenders (id) ON DELETE CASCADE
        )
    """)
    # Per-tender answers and documents are separate from reusable company-profile evidence.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_requirement_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tender_id INTEGER NOT NULL,
            requirement_id TEXT NOT NULL,
            source_quote_sha256 TEXT NOT NULL DEFAULT '',
            response_json TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, tender_id, requirement_id),
            FOREIGN KEY(tender_id) REFERENCES tenders(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_requirement_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tender_id INTEGER NOT NULL,
            requirement_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'application/pdf',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, tender_id, requirement_id, sha256),
            FOREIGN KEY(tender_id) REFERENCES tenders(id) ON DELETE CASCADE
        )
    """)

    # Additive company evidence storage. Existing fixed profile columns remain canonical
    # for the current analysis engine; these tables preserve future dynamic evidence.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_profile_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            field_value TEXT NOT NULL DEFAULT '',
            source TEXT DEFAULT 'user_entered',
            source_document_id INTEGER,
            source_excerpt TEXT DEFAULT '',
            source_page INTEGER,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, field_key),
            FOREIGN KEY (source_document_id) REFERENCES company_documents (id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            document_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            media_type TEXT DEFAULT 'application/pdf',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, document_key, version)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_document_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            value_key TEXT NOT NULL,
            value_text TEXT NOT NULL DEFAULT '',
            source_excerpt TEXT DEFAULT '',
            source_page INTEGER,
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (document_id, value_key),
            FOREIGN KEY (document_id) REFERENCES company_documents (id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_rag_indexes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tender_id INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dimension INTEGER NOT NULL,
            chunker_version TEXT NOT NULL,
            chunk_size INTEGER NOT NULL,
            chunk_overlap INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, tender_id),
            FOREIGN KEY (tender_id) REFERENCES tenders (id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tender_id INTEGER NOT NULL,
            chunk_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            start_char INTEGER NOT NULL,
            end_char INTEGER NOT NULL,
            text TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, tender_id, chunk_id),
            FOREIGN KEY (tender_id) REFERENCES tenders (id) ON DELETE CASCADE
        )
    """)

    # Dynamic profile field definitions generated from tender requirements (Section 3). One row per
    # company-level field; values stay in company_profile_fields (or a fixed company_profile column).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_profile_field_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            label TEXT NOT NULL,
            input_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            storage TEXT NOT NULL DEFAULT 'dynamic',
            document_key TEXT,
            document_label TEXT DEFAULT '',
            reusable INTEGER NOT NULL DEFAULT 1,
            origin TEXT NOT NULL DEFAULT 'generated',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, field_key)
        )
    """)
    # Tender-specific links explaining why a field exists. Kept separate from company values.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_profile_field_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            tender_id INTEGER NOT NULL,
            analysis_id INTEGER,
            requirement_id TEXT NOT NULL,
            requirement_text TEXT DEFAULT '',
            requirement_status TEXT DEFAULT '',
            is_mandatory INTEGER NOT NULL DEFAULT 1,
            source_page INTEGER,
            source_quotation TEXT DEFAULT '',
            explanation TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, field_key, tender_id, requirement_id),
            FOREIGN KEY (tender_id) REFERENCES tenders (id) ON DELETE CASCADE
        )
    """)

    # Append-only history of evidence status changes (Section 5 review path).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_evidence_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            document_value_id INTEGER,
            document_id INTEGER,
            document_version INTEGER,
            previous_status TEXT,
            new_status TEXT NOT NULL,
            method TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            reasons TEXT DEFAULT '',
            checked_page INTEGER,
            checked_excerpt TEXT DEFAULT '',
            reviewer_user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Safe Schema Migrations for existing databases
    def ensure_column(table_name, col_name, col_def):
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        if col_name not in columns:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")

    ensure_column("company_profile", "user_id", "INTEGER")
    ensure_column("tenders", "user_id", "INTEGER")
    ensure_column("tender_analyses", "user_id", "INTEGER")
    ensure_column("company_documents", "verification_status", "TEXT NOT NULL DEFAULT 'unverified'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_disclosures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tender_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            field_label TEXT DEFAULT '',
            field_value TEXT DEFAULT '',
            doc_reference TEXT DEFAULT '',
            evidence_status TEXT DEFAULT 'unverified',
            section_id INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, tender_id, field_key),
            FOREIGN KEY (tender_id) REFERENCES tenders (id) ON DELETE CASCADE
        )
    """)

    # Safe Indexes for multi-tenant query performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_company_profile_user ON company_profile(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_user ON tenders(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tender_analyses_user ON tender_analyses(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_company_profile_fields_user ON company_profile_fields(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_company_documents_user ON company_documents(user_id, document_key, version)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_company_document_values_user ON company_document_values(user_id, document_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tender_rag_indexes_owner ON tender_rag_indexes(user_id, tender_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tender_chunks_owner_page ON tender_chunks(user_id, tender_id, page_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_field_definitions_user ON company_profile_field_definitions(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_field_requirements_user ON company_profile_field_requirements(user_id, field_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tender_responses_owner ON tender_requirement_responses(user_id, tender_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tender_response_docs_owner ON tender_requirement_documents(user_id, tender_id, requirement_id)")

    conn.commit()
    conn.close()


# --- User Authentication Operations ---

def hash_password(password: str) -> str:
    """Generate a secure salted PBKDF2-SHA256 password hash."""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000
    ).hex()
    return f"{salt}${pwd_hash}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored salt and hash using constant-time comparison."""
    if not hashed or "$" not in hashed:
        return False
    try:
        salt, pwd_hash = hashed.split("$", 1)
        calc_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000
        ).hex()
        return hmac.compare_digest(pwd_hash, calc_hash)
    except Exception:
        return False


def create_user(email: str, password: str, full_name: str = "") -> tuple[bool, str, dict | None]:
    """Register a new user with email and hashed password."""
    email = (email or "").strip().lower()
    if not email or "@" not in email or "." not in email:
        return False, "Please provide a valid email address.", None
    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters long.", None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        return False, "An account with this email address already exists.", None

    pwd_hash = hash_password(password)
    cursor.execute(
        "INSERT INTO users (email, password_hash, full_name) VALUES (?, ?, ?)",
        (email, pwd_hash, (full_name or "").strip())
    )
    conn.commit()
    user_id = cursor.lastrowid

    # If this is the first registered user and there are legacy unassigned records, assign them
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    if user_count == 1:
        cursor.execute("UPDATE company_profile SET user_id = ? WHERE user_id IS NULL", (user_id,))
        cursor.execute("UPDATE tenders SET user_id = ? WHERE user_id IS NULL", (user_id,))
        cursor.execute("UPDATE tender_analyses SET user_id = ? WHERE user_id IS NULL", (user_id,))
        conn.commit()

    cursor.execute("SELECT id, email, full_name, created_at FROM users WHERE id = ?", (user_id,))
    user = dict(cursor.fetchone())
    conn.close()
    return True, "Account registered successfully.", user


def authenticate_user(email: str, password: str) -> tuple[bool, str, dict | None]:
    """Authenticate user with email and password."""
    email = (email or "").strip().lower()
    if not email or not password:
        return False, "Please enter both email and password.", None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, password_hash, full_name, created_at FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return False, "Invalid email or password.", None

    user_data = dict(row)
    if not verify_password(password, user_data.get("password_hash", "")):
        return False, "Invalid email or password.", None

    safe_user = {
        "id": user_data["id"],
        "email": user_data["email"],
        "full_name": user_data["full_name"],
        "created_at": user_data["created_at"],
    }
    return True, "Login successful.", safe_user


def get_user_by_id(user_id: int) -> dict | None:
    """Retrieve user details by ID."""
    if not user_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, full_name, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# --- User-Scoped Company Profile Operations ---

def get_company_profile(user_id: int):
    """Retrieve the company profile scoped to the authenticated user. Returns dict or None."""
    if not user_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM company_profile WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
        row = cursor.fetchone()
        profile = dict(row) if row else {
            "user_id": user_id, "company_name": "", "industry": "", "years_of_experience": "",
            "annual_turnover": "", "certifications": "", "technical_capabilities": "",
            "past_projects": "", "financial_details": "", "contact_name": "", "contact_email": "",
            "contact_phone": "", "contact_address": "",
        }
        cursor.execute("""
            SELECT field_key, field_value, source, source_document_id, source_excerpt,
                   source_page, verification_status, updated_at
            FROM company_profile_fields WHERE user_id = ? ORDER BY field_key
        """, (user_id,))
        profile["custom_fields"] = [dict(r) for r in cursor.fetchall()]
        cursor.execute("""
            SELECT id, document_key, version, filename, storage_path, media_type,
                   size_bytes, sha256, uploaded_at, verification_status
            FROM company_documents WHERE user_id = ? ORDER BY document_key, version
        """, (user_id,))
        docs = [dict(r) for r in cursor.fetchall()]
        for doc in docs:
            cursor.execute("""
                SELECT id, value_key, value_text, source_excerpt, source_page,
                       verification_status, updated_at,
                       (SELECT r.method FROM company_evidence_reviews r
                        WHERE r.document_value_id = company_document_values.id AND r.user_id = company_document_values.user_id
                          AND r.passed = 1 AND r.new_status = 'verified' ORDER BY r.id DESC LIMIT 1) AS review_method
                FROM company_document_values WHERE user_id = ? AND document_id = ? ORDER BY value_key
            """, (user_id, doc["id"]))
            doc["extracted_values"] = [dict(v) for v in cursor.fetchall()]
        profile["supporting_documents"] = docs
        return profile if row or profile["custom_fields"] or docs else None
    finally:
        conn.close()


def save_company_profile(data: dict, user_id: int):
    """Insert or update the company profile for the authenticated user."""
    if not user_id:
        raise ValueError("user_id is required to save company profile.")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM company_profile WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    existing = cursor.fetchone()
    if existing:
        merged = dict(existing)
        for k, v in data.items():
            if v is not None:
                merged[k] = v
        cursor.execute("""
            UPDATE company_profile SET
                company_name = ?,
                industry = ?,
                years_of_experience = ?,
                annual_turnover = ?,
                certifications = ?,
                technical_capabilities = ?,
                past_projects = ?,
                financial_details = ?,
                contact_name = ?,
                contact_email = ?,
                contact_phone = ?,
                contact_address = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (
            merged.get("company_name", ""),
            merged.get("industry", ""),
            merged.get("years_of_experience", ""),
            merged.get("annual_turnover", ""),
            merged.get("certifications", ""),
            merged.get("technical_capabilities", ""),
            merged.get("past_projects", ""),
            merged.get("financial_details", ""),
            merged.get("contact_name", ""),
            merged.get("contact_email", ""),
            merged.get("contact_phone", ""),
            merged.get("contact_address", ""),
            existing["id"],
            user_id,
        ))
    else:
        cursor.execute("""
            INSERT INTO company_profile (
                user_id, company_name, industry, years_of_experience, annual_turnover,
                certifications, technical_capabilities, past_projects,
                financial_details, contact_name, contact_email,
                contact_phone, contact_address
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            data.get("company_name", ""),
            data.get("industry", ""),
            data.get("years_of_experience", ""),
            data.get("annual_turnover", ""),
            data.get("certifications", ""),
            data.get("technical_capabilities", ""),
            data.get("past_projects", ""),
            data.get("financial_details", ""),
            data.get("contact_name", ""),
            data.get("contact_email", ""),
            data.get("contact_phone", ""),
            data.get("contact_address", ""),
        ))

    conn.commit()
    conn.close()


def save_company_profile_fields(user_id: int, fields: list[dict]) -> None:
    """Upsert user-defined profile fields with optional evidence provenance."""
    if not user_id:
        raise ValueError("user_id is required to save dynamic profile fields.")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for field in fields or []:
            key = " ".join(str(field.get("field_key") or "").split())[:120]
            value = str(field.get("field_value") or "").strip()
            if not key:
                continue
            status = str(field.get("verification_status") or "unverified").lower()
            if status not in COMPANY_EVIDENCE_STATUSES:
                raise ValueError(f"Unsupported verification status: {status}")
            source_document_id = field.get("source_document_id")
            if source_document_id is not None:
                cursor.execute("SELECT 1 FROM company_documents WHERE id = ? AND user_id = ?", (source_document_id, user_id))
                if not cursor.fetchone():
                    raise ValueError("Source document does not belong to this user.")
            cursor.execute("""
                INSERT INTO company_profile_fields (
                    user_id, field_key, field_value, source, source_document_id,
                    source_excerpt, source_page, verification_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, field_key) DO UPDATE SET
                    field_value = excluded.field_value, source = excluded.source,
                    source_document_id = excluded.source_document_id,
                    source_excerpt = excluded.source_excerpt, source_page = excluded.source_page,
                    verification_status = excluded.verification_status, updated_at = CURRENT_TIMESTAMP
            """, (user_id, key, value, str(field.get("source") or "user_entered"), source_document_id,
                  str(field.get("source_excerpt") or ""), field.get("source_page"), status))
        conn.commit()
    finally:
        conn.close()


def delete_company_profile_field(user_id: int, field_key: str) -> None:
    """Delete one dynamic field owned by the specified user."""
    if not user_id:
        raise ValueError("user_id is required to delete a dynamic profile field.")
    conn = get_connection()
    try:
        conn.execute("DELETE FROM company_profile_fields WHERE user_id = ? AND field_key = ?", (user_id, field_key))
        conn.commit()
    finally:
        conn.close()


def save_company_document(user_id: int, filename: str, content: bytes, media_type: str = "application/pdf",
                          document_key: str | None = None) -> int:
    """Persist an uploaded supporting document and append a versioned, user-scoped metadata row."""
    if not user_id:
        raise ValueError("user_id is required to save a company document.")
    if not isinstance(content, bytes) or not content:
        raise ValueError("Company document content must be non-empty bytes.")
    safe_name = re.sub(r"[^A-Za-z0-9._() -]", "_", str(filename).replace("\\", "/").split("/")[-1]).strip(" .")
    if not safe_name:
        raise ValueError("A valid company document filename is required.")
    key = str(document_key or safe_name).strip()[:200]
    if not key:
        raise ValueError("A document key is required.")
    digest = hashlib.sha256(content).hexdigest()
    conn = get_connection()
    storage_path = None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, sha256, storage_path FROM company_documents
            WHERE user_id = ? AND document_key = ? ORDER BY version DESC LIMIT 1
        """, (user_id, key))
        latest = cursor.fetchone()
        if latest and latest["sha256"] == digest and os.path.isfile(latest["storage_path"]):
            return latest["id"]
        cursor.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM company_documents WHERE user_id = ? AND document_key = ?",
                       (user_id, key))
        version = cursor.fetchone()[0]
        user_dir = os.path.join(DATA_DIR, "company_documents", str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        storage_path = os.path.join(user_dir, f"{uuid.uuid4().hex}_{safe_name}")
        with open(storage_path, "xb") as output:
            output.write(content)
        cursor.execute("""
            INSERT INTO company_documents (
                user_id, document_key, version, filename, storage_path, media_type,
                size_bytes, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, key, version, safe_name, storage_path, media_type, len(content), digest))
        doc_id = cursor.lastrowid
        conn.commit()
        return doc_id
    except Exception:
        conn.rollback()
        if storage_path and os.path.exists(storage_path):
            os.remove(storage_path)
        raise
    finally:
        conn.close()


def get_company_documents(user_id: int) -> list[dict]:
    """List supporting document versions and extracted values owned by one user."""
    if not user_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, document_key, version, filename, storage_path, media_type,
                   size_bytes, sha256, uploaded_at, verification_status
            FROM company_documents WHERE user_id = ? ORDER BY document_key, version DESC
        """, (user_id,))
        docs = [dict(r) for r in cursor.fetchall()]
        for doc in docs:
            cursor.execute("""
                SELECT id, value_key, value_text, source_excerpt, source_page,
                       verification_status, updated_at,
                       (SELECT r.method FROM company_evidence_reviews r
                        WHERE r.document_value_id = company_document_values.id AND r.user_id = company_document_values.user_id
                          AND r.passed = 1 AND r.new_status = 'verified' ORDER BY r.id DESC LIMIT 1) AS review_method
                FROM company_document_values WHERE user_id = ? AND document_id = ? ORDER BY value_key
            """, (user_id, doc["id"]))
            doc["extracted_values"] = [dict(v) for v in cursor.fetchall()]
        return docs
    finally:
        conn.close()


def save_company_document_values(user_id: int, document_id: int, values: list[dict]) -> None:
    """Store extracted document values and their source excerpts/status under the document owner."""
    if not user_id:
        raise ValueError("user_id is required to save extracted company evidence.")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM company_documents WHERE id = ? AND user_id = ?", (document_id, user_id))
        if not cursor.fetchone():
            raise ValueError("Company document does not belong to this user.")
        for item in values or []:
            key = " ".join(str(item.get("value_key") or "").split())[:120]
            value = str(item.get("value_text") or "").strip()
            if not key or not value:
                continue
            status = str(item.get("verification_status") or "unverified").lower()
            if status not in COMPANY_EVIDENCE_STATUSES:
                raise ValueError(f"Unsupported verification status: {status}")
            cursor.execute("""
                INSERT INTO company_document_values (
                    user_id, document_id, value_key, value_text, source_excerpt,
                    source_page, verification_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(document_id, value_key) DO UPDATE SET
                    value_text = excluded.value_text, source_excerpt = excluded.source_excerpt,
                    source_page = excluded.source_page, verification_status = excluded.verification_status,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, document_id, key, value, str(item.get("source_excerpt") or ""),
                  item.get("source_page"), status))
        conn.commit()
    finally:
        conn.close()


def update_company_document_value_status(user_id: int, value_id: int, verification_status: str) -> None:
    """Update an extracted value's verification status without crossing user boundaries."""
    status = str(verification_status or "").lower()
    if status not in COMPANY_EVIDENCE_STATUSES:
        raise ValueError(f"Unsupported verification status: {status}")
    if not user_id:
        raise ValueError("user_id is required to update extracted company evidence.")
    conn = get_connection()
    try:
        cursor = conn.execute("""
            UPDATE company_document_values SET verification_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (status, value_id, user_id))
        if not cursor.rowcount:
            raise ValueError("Extracted value does not belong to this user.")
        conn.commit()
    finally:
        conn.close()


def update_company_document_status(user_id: int, document_id: int, verification_status: str) -> None:
    """Set a document version's review status (never set automatically on upload)."""
    status = str(verification_status or "").lower()
    if status not in COMPANY_EVIDENCE_STATUSES:
        raise ValueError(f"Unsupported verification status: {status}")
    if not user_id:
        raise ValueError("user_id is required to update a company document.")
    conn = get_connection()
    try:
        cursor = conn.execute("UPDATE company_documents SET verification_status = ? WHERE id = ? AND user_id = ?",
                              (status, document_id, user_id))
        if not cursor.rowcount:
            raise ValueError("Company document does not belong to this user.")
        conn.commit()
    finally:
        conn.close()


# --- Evidence review (the only UI path that can set an extracted value to 'verified') ---

REVIEW_METHOD_DOCUMENT_CHECK = "document_excerpt_check"
REVIEW_METHOD_WITHDRAWN = "user_withdrawn"


def _review_norm(text) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower().replace(",", "")))


def _value_supported_by_excerpt(value: str, excerpt: str) -> tuple[bool, str]:
    """The claimed value must be stated in the excerpt: either verbatim, or all its figures/standards appear."""
    nv, ne = _review_norm(value), _review_norm(excerpt)
    if nv and f" {nv} " in f" {ne} ":
        return True, ""
    figures = re.findall(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not figures:
        return False, "the value is not stated in the excerpt"
    missing = [f for f in figures if not re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", str(excerpt).replace(",", ""))]
    if missing:
        return False, f"the excerpt does not contain the value's figure(s): {', '.join(missing)}"
    return True, ""


def _record_review(cursor, user_id, value_row, new_status, method, passed, reasons, page, excerpt):
    cursor.execute("""
        INSERT INTO company_evidence_reviews (user_id, document_value_id, document_id, document_version, previous_status,
            new_status, method, passed, reasons, checked_page, checked_excerpt, reviewer_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, value_row["id"], value_row["document_id"], value_row["version"], value_row["verification_status"],
          new_status, method, 1 if passed else 0, "; ".join(reasons), page, str(excerpt or "")[:2000], user_id))


def review_document_value(user_id: int, value_id: int) -> dict:
    """
    Check an extracted value against its uploaded document version and record the review.
    The value becomes 'verified' only if ALL of these hold:
      * the value and document belong to the user and the document version is not rejected;
      * a source page and an excerpt (>= 15 characters) were recorded for the value;
      * the excerpt is found, word for word, in the text of that page of the stored PDF;
      * the excerpt states the value (verbatim, or all of the value's figures).
    This is a document check performed in-app on the user's own upload — not independent third-party verification.
    Every attempt (pass or fail) is appended to company_evidence_reviews.
    """
    import fitz  # local import: only the review path reads stored PDFs

    if not user_id:
        raise ValueError("user_id is required to review company evidence.")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT v.id, v.document_id, v.value_text, v.source_excerpt, v.source_page, v.verification_status,
                   d.version, d.storage_path, d.verification_status AS document_status, d.filename
            FROM company_document_values v JOIN company_documents d ON d.id = v.document_id AND d.user_id = v.user_id
            WHERE v.id = ? AND v.user_id = ?
        """, (value_id, user_id))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Extracted value does not belong to this user.")
        reasons, page_text = [], ""
        excerpt, page = str(row["source_excerpt"] or "").strip(), row["source_page"]
        if row["document_status"] == "rejected":
            reasons.append("the document version was rejected")
        if len(excerpt) < 15:
            reasons.append("record the exact supporting text from the document (at least 15 characters)")
        if not page:
            reasons.append("record the page of the document that contains the supporting text")
        if not reasons:
            try:
                with fitz.open(row["storage_path"]) as pdf:
                    if not (1 <= int(page) <= len(pdf)):
                        reasons.append(f"page {page} does not exist in {row['filename']} (it has {len(pdf)} page(s))")
                    else:
                        page_text = pdf[int(page) - 1].get_text("text")
            except Exception as exc:
                reasons.append(f"the stored document could not be read as a PDF ({str(exc)[:120]})")
        if not reasons:
            if not page_text.strip():
                reasons.append(f"page {page} has no extractable text (scanned pages cannot be checked; OCR is not available)")
            elif f" {_review_norm(excerpt)} " not in f" {_review_norm(page_text)} ":
                reasons.append(f"the supporting text was not found on page {page} of {row['filename']} v{row['version']}")
            else:
                ok, why = _value_supported_by_excerpt(row["value_text"], excerpt)
                if not ok:
                    reasons.append(why)
        passed = not reasons
        new_status = "verified" if passed else row["verification_status"]
        if passed:
            cursor.execute("UPDATE company_document_values SET verification_status = 'verified', updated_at = CURRENT_TIMESTAMP "
                           "WHERE id = ? AND user_id = ?", (value_id, user_id))
        _record_review(cursor, user_id, row, new_status, REVIEW_METHOD_DOCUMENT_CHECK, passed, reasons, page, excerpt)
        conn.commit()
        return {"passed": passed, "status": new_status, "reasons": reasons,
                "document_id": row["document_id"], "document_version": row["version"]}
    finally:
        conn.close()


def withdraw_document_value(user_id: int, value_id: int, note: str = "") -> None:
    """Mark a value as rejected by its owner (e.g. wrong figure). Recorded in the review history."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT v.id, v.document_id, v.verification_status, d.version FROM company_document_values v
            JOIN company_documents d ON d.id = v.document_id WHERE v.id = ? AND v.user_id = ?
        """, (value_id, user_id))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Extracted value does not belong to this user.")
        cursor.execute("UPDATE company_document_values SET verification_status = 'rejected', updated_at = CURRENT_TIMESTAMP "
                       "WHERE id = ? AND user_id = ?", (value_id, user_id))
        _record_review(cursor, user_id, row, "rejected", REVIEW_METHOD_WITHDRAWN, True, [note] if note else [], None, "")
        conn.commit()
    finally:
        conn.close()


def get_evidence_reviews(user_id: int, document_value_id: int | None = None) -> list[dict]:
    """Review history for the user's evidence (newest first)."""
    if not user_id:
        return []
    conn = get_connection()
    try:
        sql = "SELECT * FROM company_evidence_reviews WHERE user_id = ?"
        args = [user_id]
        if document_value_id is not None:
            sql += " AND document_value_id = ?"
            args.append(document_value_id)
        return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC", args).fetchall()]
    finally:
        conn.close()


# --- Dynamic Profile Field Definitions (generated from tender requirements) ---

FIELD_INPUT_TYPES = ("money_period", "years", "text", "long_text", "certificate", "document")
_DEFINITION_COLUMNS = ("field_key", "label", "input_type", "description", "storage", "document_key",
                       "document_label", "reusable", "origin")


def sync_tender_profile_fields(user_id: int, tender_id: int, analysis_id: int | None,
                               definitions: list[dict], links: list[dict]) -> dict:
    """
    Atomically record the profile fields one tender's analysis needs.
      * Definitions are created only when the stable field_key is new for this user; an existing definition
        (and any value saved for it) is reused unchanged, so repeated analyses never duplicate fields.
      * Links (field_key, tender_id, requirement_id) are upserted; links of this tender that the latest
        analysis no longer produces are deactivated, not deleted.
    Company values are not touched here.
    """
    if not user_id or not tender_id:
        raise ValueError("user_id and tender_id are required to sync profile fields.")
    conn = get_connection()
    created, reused = [], []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM tenders WHERE id = ? AND user_id = ?", (tender_id, user_id))
        if not cursor.fetchone():
            raise PermissionError("Tender not found for this user.")
        for d in definitions or []:
            key = str(d.get("field_key") or "").strip()
            if not key:
                continue
            if d.get("input_type") not in FIELD_INPUT_TYPES:
                raise ValueError(f"Unsupported field input type: {d.get('input_type')}")
            cursor.execute("SELECT 1 FROM company_profile_field_definitions WHERE user_id = ? AND field_key = ?", (user_id, key))
            if cursor.fetchone():
                reused.append(key)
                continue
            cursor.execute(f"""
                INSERT INTO company_profile_field_definitions (user_id, {', '.join(_DEFINITION_COLUMNS)})
                VALUES (?, {', '.join('?' for _ in _DEFINITION_COLUMNS)})
            """, (user_id, key, str(d.get("label") or key), d["input_type"], str(d.get("description") or ""),
                  str(d.get("storage") or "dynamic"), d.get("document_key"), str(d.get("document_label") or ""),
                  1 if d.get("reusable", True) else 0, str(d.get("origin") or "generated")))
            created.append(key)
        keep = set()
        for link in links or []:
            key, rid = str(link.get("field_key") or "").strip(), str(link.get("requirement_id") or "").strip()
            if not key or not rid:
                continue
            keep.add((key, rid))
            cursor.execute("""
                INSERT INTO company_profile_field_requirements (
                    user_id, field_key, tender_id, analysis_id, requirement_id, requirement_text, requirement_status,
                    is_mandatory, source_page, source_quotation, explanation, active, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, field_key, tender_id, requirement_id) DO UPDATE SET
                    analysis_id = excluded.analysis_id, requirement_text = excluded.requirement_text,
                    requirement_status = excluded.requirement_status, is_mandatory = excluded.is_mandatory,
                    source_page = excluded.source_page, source_quotation = excluded.source_quotation,
                    explanation = excluded.explanation, active = 1, updated_at = CURRENT_TIMESTAMP
            """, (user_id, key, tender_id, analysis_id, rid, str(link.get("requirement_text") or ""),
                  str(link.get("requirement_status") or ""), 1 if link.get("is_mandatory", True) else 0,
                  link.get("source_page"), str(link.get("source_quotation") or "")[:1200],
                  str(link.get("explanation") or "")))
        cursor.execute("SELECT id, field_key, requirement_id FROM company_profile_field_requirements "
                       "WHERE user_id = ? AND tender_id = ? AND active = 1", (user_id, tender_id))
        stale = [r["id"] for r in cursor.fetchall() if (r["field_key"], r["requirement_id"]) not in keep]
        for link_id in stale:
            cursor.execute("UPDATE company_profile_field_requirements SET active = 0, updated_at = CURRENT_TIMESTAMP "
                           "WHERE id = ? AND user_id = ?", (link_id, user_id))
        conn.commit()
        return {"created": created, "reused": reused, "links": len(keep), "deactivated": len(stale)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_profile_field_definitions(user_id: int, include_inactive_links: bool = False) -> list[dict]:
    """The user's dynamic field definitions, each with its tender requirement links."""
    if not user_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM company_profile_field_definitions WHERE user_id = ? ORDER BY field_key", (user_id,))
        definitions = [dict(r) for r in cursor.fetchall()]
        for d in definitions:
            cursor.execute(f"""
                SELECT l.*, t.filename AS tender_filename FROM company_profile_field_requirements l
                JOIN tenders t ON t.id = l.tender_id AND t.user_id = l.user_id
                WHERE l.user_id = ? AND l.field_key = ? {'' if include_inactive_links else 'AND l.active = 1'}
                ORDER BY l.tender_id, l.requirement_id
            """, (user_id, d["field_key"]))
            d["requirements"] = [dict(r) for r in cursor.fetchall()]
            d["required"] = any(r["is_mandatory"] and r["active"] for r in d["requirements"])
            d["reusable"] = bool(d["reusable"])
        return definitions
    finally:
        conn.close()


# --- User-Scoped Tender Operations ---

def save_tender(filename: str, filepath: str, extracted_text: str,
                page_count: int, upload_status: str = "success", user_id: int = None):
    """Save tender metadata scoped to the authenticated user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tenders (user_id, filename, filepath, extracted_text, page_count, upload_status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, filename, filepath, extracted_text, page_count, upload_status))
    conn.commit()
    tender_id = cursor.lastrowid
    conn.close()
    return tender_id


def get_all_tenders(user_id: int):
    """Retrieve all uploaded tenders for the authenticated user."""
    if not user_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tenders WHERE user_id = ? ORDER BY uploaded_at DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_tender_by_id(tender_id: int, user_id: int):
    """Retrieve a single tender by its ID, ensuring owner isolation."""
    if not user_id or not tender_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tenders WHERE id = ? AND user_id = ?", (tender_id, user_id))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


# --- Legacy Tender Disclosure Compatibility ---

def save_tender_disclosure(user_id: int, tender_id: int, field_key: str, field_label: str,
                           field_value: str, doc_reference: str = "", evidence_status: str = "unverified",
                           section_id: int = 0) -> bool:
    if not user_id or not tender_id or not str(field_key or "").strip():
        return False
    conn = get_connection()
    tender = conn.execute("SELECT id FROM tenders WHERE id = ?", (tender_id,)).fetchone()
    if not tender:
        conn.close()
        return False
    conn.execute("""
        INSERT INTO tender_disclosures
            (user_id, tender_id, field_key, field_label, field_value, doc_reference, evidence_status, section_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, tender_id, field_key) DO UPDATE SET
            field_label=excluded.field_label,
            field_value=excluded.field_value,
            doc_reference=excluded.doc_reference,
            evidence_status=excluded.evidence_status,
            section_id=excluded.section_id,
            updated_at=CURRENT_TIMESTAMP
    """, (user_id, tender_id, field_key, field_label, field_value, doc_reference, evidence_status, section_id))
    conn.commit()
    conn.close()
    return True


def get_tender_disclosures(user_id: int, tender_id: int) -> dict:
    if not user_id or not tender_id:
        return {}
    conn = get_connection()
    rows = conn.execute(
        "SELECT field_key, field_label, field_value, doc_reference, evidence_status, section_id FROM tender_disclosures WHERE user_id = ? AND tender_id = ?",
        (user_id, tender_id),
    ).fetchall()
    conn.close()
    return {row["field_key"]: dict(row) for row in rows}


def get_all_user_disclosures(user_id: int) -> dict:
    if not user_id:
        return {}
    conn = get_connection()
    rows = conn.execute(
        "SELECT field_key, field_label, field_value, doc_reference, evidence_status, section_id FROM tender_disclosures WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return {row["field_key"]: dict(row) for row in rows}


# --- User-Scoped Tender Analysis Operations ---

def compute_profile_hash(profile: dict | None) -> str:
    """Hash fixed and dynamic profile evidence, including supporting-document versions."""
    if not profile:
        return "empty_profile"
    relevant_keys = [
        "company_name", "industry", "years_of_experience", "annual_turnover",
        "certifications", "technical_capabilities", "past_projects", "financial_details"
    ]
    extracted = {k: str(profile.get(k, "")).strip().lower() for k in relevant_keys}
    custom_fields = profile.get("custom_fields") or []
    if custom_fields:
        extracted["custom_fields"] = sorted(({
            "field_key": str(f.get("field_key", "")).strip(),
            "field_value": str(f.get("field_value", "")).strip(),
            "source": str(f.get("source", "")),
            "source_document_id": f.get("source_document_id"),
            "source_excerpt": str(f.get("source_excerpt", "")).strip(),
            "source_page": f.get("source_page"),
            "verification_status": str(f.get("verification_status", "unverified")),
        } for f in custom_fields), key=lambda f: (f["field_key"], f["field_value"]))
    documents = profile.get("supporting_documents") or []
    if documents:
        extracted["supporting_documents"] = sorted(({
            "document_key": str(d.get("document_key", "")),
            "version": int(d.get("version", 1)),
            "sha256": str(d.get("sha256", "")),
            # Only a non-default review status is hashed, so existing hashes stay stable after the column was added.
            **({"verification_status": str(d.get("verification_status"))}
               if str(d.get("verification_status") or "unverified") != "unverified" else {}),
            "extracted_values": sorted(({
                "value_key": str(v.get("value_key", "")),
                "value_text": str(v.get("value_text", "")).strip(),
                "source_excerpt": str(v.get("source_excerpt", "")).strip(),
                "source_page": v.get("source_page"),
                "verification_status": str(v.get("verification_status", "unverified")),
            } for v in d.get("extracted_values", [])), key=lambda v: (v["value_key"], v["value_text"]))
        } for d in documents), key=lambda d: (d["document_key"], d["version"], d["sha256"]))
    serialized = json.dumps(extracted, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def save_analysis(tender_id: int, profile_hash: str, recommendation: str,
                  executive_summary: str, structured_tender_data: dict,
                  compliance_matrix: list, risks: list,
                  missing_information: list, model_used: str = "", user_id: int = None) -> int:
    """Save or update tender analysis scoped to the user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tender_analyses (
            user_id, tender_id, profile_hash, recommendation, executive_summary,
            structured_tender_data, compliance_matrix, risks,
            missing_information, model_used
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        tender_id,
        profile_hash,
        recommendation,
        executive_summary,
        json.dumps(structured_tender_data),
        json.dumps(compliance_matrix),
        json.dumps(risks),
        json.dumps(missing_information),
        model_used,
    ))
    conn.commit()
    analysis_id = cursor.lastrowid
    conn.close()
    return analysis_id


def get_analysis_for_tender(tender_id: int, user_id: int) -> dict | None:
    """Retrieve the latest analysis for a tender scoped to the authenticated user."""
    if not user_id or not tender_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM tender_analyses 
        WHERE tender_id = ? AND user_id = ?
        ORDER BY analyzed_at DESC, id DESC LIMIT 1
    """, (tender_id, user_id))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    try:
        data["structured_tender_data"] = json.loads(data.get("structured_tender_data") or "{}")
    except Exception:
        data["structured_tender_data"] = {}
    try:
        data["compliance_matrix"] = json.loads(data.get("compliance_matrix") or "[]")
    except Exception:
        data["compliance_matrix"] = []
    try:
        data["risks"] = json.loads(data.get("risks") or "[]")
    except Exception:
        data["risks"] = []
    try:
        data["missing_information"] = json.loads(data.get("missing_information") or "[]")
    except Exception:
        data["missing_information"] = []
    return data


def save_tender_requirement_response(user_id: int, tender_id: int, requirement_id: str,
                                    source_quotation: str, response: dict,
                                    document: dict | None = None) -> dict:
    """Save unverified, tender-scoped response data and optional supporting PDF metadata/file."""
    if not user_id or not tender_id or not str(requirement_id or "").strip():
        raise ValueError("A user, tender and requirement are required.")
    if not isinstance(response, dict):
        raise ValueError("Requirement response must be a structured object.")
    quote_hash = hashlib.sha256(" ".join(str(source_quotation or "").split()).encode("utf-8")).hexdigest()
    safe_requirement_id = " ".join(str(requirement_id).split())[:120]
    safe_name, content, media_type = "", b"", "application/pdf"
    digest = ""
    if document:
        content = document.get("content") or b""
        if not isinstance(content, bytes) or not content:
            raise ValueError("Uploaded file is empty or invalid.")
        if len(content) > 50 * 1024 * 1024:
            raise ValueError("Uploaded file exceeds the 50 MB limit.")
        safe_name = re.sub(r"[^A-Za-z0-9._() -]", "_", str(document.get("filename") or "evidence.pdf").replace("\\", "/").split("/")[-1]).strip(" .")
        if not safe_name.lower().endswith(".pdf"):
            raise ValueError("Only PDF supporting documents are accepted.")
        media_type = str(document.get("media_type") or "application/pdf")[:100]
        digest = hashlib.sha256(content).hexdigest()

    conn = get_connection()
    stored_path = None
    try:
        owner = conn.execute("SELECT id FROM tenders WHERE id = ? AND user_id = ?", (tender_id, user_id)).fetchone()
        if not owner:
            raise PermissionError("Tender is not available to this user.")
        current = conn.execute("""SELECT * FROM tender_requirement_responses
                                  WHERE user_id = ? AND tender_id = ? AND requirement_id = ?""",
                               (user_id, tender_id, safe_requirement_id)).fetchone()
        if document:
            existing = conn.execute("""SELECT id, storage_path FROM tender_requirement_documents
                                        WHERE user_id = ? AND tender_id = ? AND requirement_id = ? AND sha256 = ?""",
                                    (user_id, tender_id, safe_requirement_id, digest)).fetchone()
            if not (existing and os.path.isfile(existing["storage_path"])):
                user_dir = os.path.join(DATA_DIR, "tender_requirement_documents", str(user_id), str(tender_id))
                os.makedirs(user_dir, exist_ok=True)
                stored_path = os.path.join(user_dir, f"{digest}_{safe_name}")
                with open(stored_path, "xb") as output:
                    output.write(content)
                conn.execute("""INSERT OR IGNORE INTO tender_requirement_documents
                    (user_id, tender_id, requirement_id, filename, storage_path, media_type, size_bytes, sha256)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, tender_id, safe_requirement_id, safe_name, stored_path,
                     media_type, len(content), digest))
        payload = json.dumps(response, ensure_ascii=False)
        if current:
            conn.execute("""UPDATE tender_requirement_responses
                             SET source_quote_sha256 = ?, response_json = ?, updated_at = CURRENT_TIMESTAMP
                             WHERE id = ? AND user_id = ?""",
                         (quote_hash, payload, current["id"], user_id))
            response_id = current["id"]
        else:
            cursor = conn.execute("""INSERT INTO tender_requirement_responses
                (user_id, tender_id, requirement_id, source_quote_sha256, response_json)
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, tender_id, safe_requirement_id, quote_hash, payload))
            response_id = cursor.lastrowid
        conn.commit()
        return {"id": response_id, "requirement_id": safe_requirement_id,
                "verification_status": "unverified", "document_saved": bool(document)}
    except Exception:
        conn.rollback()
        if stored_path and os.path.exists(stored_path):
            os.remove(stored_path)
        raise
    finally:
        conn.close()


def get_tender_requirement_responses(user_id: int, tender_id: int) -> dict[str, dict]:
    """Fetch only this user's saved tender-specific answers and evidence documents."""
    if not user_id or not tender_id:
        return {}
    conn = get_connection()
    try:
        rows = conn.execute("""SELECT * FROM tender_requirement_responses
                               WHERE user_id = ? AND tender_id = ?""", (user_id, tender_id)).fetchall()
        result = {}
        for row in rows:
            try:
                response = json.loads(row["response_json"] or "{}")
            except (TypeError, ValueError):
                response = {}
            result[row["requirement_id"]] = {**dict(row), "response": response, "documents": []}
        docs = conn.execute("""SELECT * FROM tender_requirement_documents
                               WHERE user_id = ? AND tender_id = ? ORDER BY uploaded_at DESC, id DESC""",
                            (user_id, tender_id)).fetchall()
        for doc in docs:
            item = result.setdefault(doc["requirement_id"], {"requirement_id": doc["requirement_id"],
                                                              "response": {}, "documents": []})
            item["documents"].append(dict(doc))
        return result
    finally:
        conn.close()


def get_dashboard_stats(user_id: int) -> dict:
    """Retrieve aggregate metrics for the dashboard using real database values scoped to the user."""
    if not user_id:
        return {
            "total_tenders": 0,
            "analyzed_tenders": 0,
            "bid_count": 0,
            "no_bid_count": 0,
            "review_count": 0,
        }

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM tenders WHERE user_id = ?", (user_id,))
    total_tenders = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT tender_id) FROM tender_analyses WHERE user_id = ?", (user_id,))
    analyzed_tenders = cursor.fetchone()[0]

    # Get the latest recommendation for each analyzed tender belonging to the user
    cursor.execute("""
        SELECT ta.recommendation
        FROM tender_analyses ta
        INNER JOIN (
            SELECT MAX(id) as latest_id
            FROM tender_analyses
            WHERE user_id = ?
            GROUP BY tender_id
        ) latest ON ta.id = latest.latest_id
        WHERE ta.user_id = ?
    """, (user_id, user_id))
    rows = cursor.fetchall()
    conn.close()

    bid_count = 0
    no_bid_count = 0
    review_count = 0
    for r in rows:
        rec = (r[0] or "").upper().strip()
        if rec == "BID":
            bid_count += 1
        elif rec == "NO-BID":
            no_bid_count += 1
        elif "REVIEW" in rec:
            review_count += 1

    return {
        "total_tenders": total_tenders,
        "analyzed_tenders": analyzed_tenders,
        "bid_count": bid_count,
        "no_bid_count": no_bid_count,
        "review_count": review_count,
    }


def get_tenders_with_analysis(user_id: int) -> list[dict]:
    """Retrieve all tenders with their latest analysis status scoped to the user."""
    if not user_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            t.id, t.filename, t.filepath, t.page_count, t.upload_status, t.uploaded_at,
            ta.id as analysis_id, ta.recommendation, ta.analyzed_at, ta.profile_hash
        FROM tenders t
        LEFT JOIN tender_analyses ta ON ta.id = (
            SELECT id FROM tender_analyses
            WHERE tender_id = t.id AND user_id = ?
            ORDER BY analyzed_at DESC, id DESC LIMIT 1
        )
        WHERE t.user_id = ?
        ORDER BY t.uploaded_at DESC
    """, (user_id, user_id))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- User-Curated Tender Requirements ---

def get_custom_requirements(tender_id: int, user_id: int) -> list[dict] | None:
    """Return the user's saved requirement list for a tender, or None if never customised."""
    if not user_id or not tender_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT requirements FROM tender_requirements WHERE tender_id = ? AND user_id = ?",
        (tender_id, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    try:
        data = json.loads(row["requirements"] or "[]")
        return data if isinstance(data, list) else None
    except Exception:
        return None


def save_custom_requirements(tender_id: int, user_id: int, requirements: list[dict]) -> None:
    """Insert or replace the user's curated requirement list for a tender (owner-scoped)."""
    if not user_id or not tender_id:
        raise ValueError("tender_id and user_id are required to save requirements.")
    if not get_tender_by_id(tender_id, user_id):
        raise PermissionError("Tender not found for this user.")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tender_requirements (user_id, tender_id, requirements, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tender_id, user_id) DO UPDATE SET
            requirements = excluded.requirements,
            updated_at = CURRENT_TIMESTAMP
    """, (user_id, tender_id, json.dumps(requirements)))
    conn.commit()
    conn.close()


def clear_custom_requirements(tender_id: int, user_id: int) -> None:
    """Remove the user's curated requirements so auto-extraction is used again."""
    if not user_id or not tender_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tender_requirements WHERE tender_id = ? AND user_id = ?", (tender_id, user_id))
    conn.commit()
    conn.close()
