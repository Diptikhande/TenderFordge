"""Phase 1 company evidence storage tests (all data uses tf_test_env's isolated DB)."""

import os
import sqlite3
import uuid

import pytest

import tf_test_env  # noqa: F401 (must run before project imports)
from database import (
    compute_profile_hash,
    create_user,
    delete_company_profile_field,
    get_company_documents,
    get_company_profile,
    get_connection,
    init_db,
    save_analysis,
    save_company_document,
    save_company_document_values,
    save_company_profile,
    save_company_profile_fields,
    save_tender,
    update_company_document_value_status,
)


@pytest.fixture
def users():
    init_db()
    users = []
    suffix = uuid.uuid4().hex
    for label in ("a", "b"):
        email = f"phase1_{label}_{suffix}@tenderforge.test"
        ok, message, user = create_user(email, "Phase1Password123!", email.split("@")[0])
        assert ok, message
        users.append(user["id"])
    yield users


def test_dynamic_fields_and_document_evidence_persist(users):
    user_id, _ = users
    save_company_profile({"company_name": "Evidence Co", "annual_turnover": "INR 3 crore"}, user_id)
    save_company_profile_fields(user_id, [{"field_key": "quality_rating", "field_value": "Grade A"}])

    content = b"Synthetic audited statement for isolated test only"
    doc_id = save_company_document(user_id, "audit.pdf", content)
    assert save_company_document(user_id, "audit.pdf", content) == doc_id  # duplicate upload is idempotent
    path = get_company_documents(user_id)[0]["storage_path"]
    assert os.path.isfile(path) and open(path, "rb").read() == content

    save_company_document_values(user_id, doc_id, [{
        "value_key": "fy2025_turnover", "value_text": "INR 3 crore",
        "source_excerpt": "Total revenue: INR 3 crore", "source_page": 4,
        "verification_status": "unverified",
    }])
    profile = get_company_profile(user_id)
    assert profile["company_name"] == "Evidence Co"  # fixed fields remain intact
    assert profile["custom_fields"][0]["field_value"] == "Grade A"
    value = profile["supporting_documents"][0]["extracted_values"][0]
    assert (value["value_text"], value["source_excerpt"], value["source_page"], value["verification_status"]) == (
        "INR 3 crore", "Total revenue: INR 3 crore", 4, "unverified")

    reopened = get_connection()
    try:
        assert reopened.execute("SELECT COUNT(*) FROM company_documents WHERE user_id = ?", (user_id,)).fetchone()[0] == 1
    finally:
        reopened.close()


def test_company_documents_and_values_are_user_scoped(users):
    user_a, user_b = users
    save_company_profile_fields(user_a, [{"field_key": "private_capability", "field_value": "A-only"}])
    doc_id = save_company_document(user_a, "secret.pdf", b"User A evidence")
    save_company_document_values(user_a, doc_id, [{"value_key": "secret", "value_text": "A-only value"}])
    value_id = get_company_documents(user_a)[0]["extracted_values"][0]["id"]

    assert get_company_documents(user_b) == []
    assert get_company_profile(user_b) is None
    assert get_company_profile(user_a)["custom_fields"][0]["field_value"] == "A-only"
    with pytest.raises(ValueError, match="does not belong"):
        save_company_document_values(user_b, doc_id, [{"value_key": "cross", "value_text": "No"}])
    with pytest.raises(ValueError, match="does not belong"):
        update_company_document_value_status(user_b, value_id, "verified")
    assert get_company_documents(user_a)[0]["extracted_values"][0]["verification_status"] == "unverified"


def test_dynamic_values_document_versions_and_verification_invalidate_analysis(users):
    user_id, _ = users
    save_company_profile({"company_name": "Freshness Co"}, user_id)
    tender_id = save_tender("phase1.pdf", "isolated/phase1.pdf", "test tender text", 1, user_id=user_id)
    initial_profile = get_company_profile(user_id)
    initial_hash = compute_profile_hash(initial_profile)
    save_analysis(tender_id, initial_hash, "REVIEW REQUIRED", "initial", {}, [], [], [], user_id=user_id)

    save_company_profile_fields(user_id, [{"field_key": "insurance_limit", "field_value": "USD 1M"}])
    with_dynamic = get_company_profile(user_id)
    dynamic_hash = compute_profile_hash(with_dynamic)
    assert dynamic_hash != initial_hash
    delete_company_profile_field(user_id, "insurance_limit")
    assert compute_profile_hash(get_company_profile(user_id)) == initial_hash
    save_company_profile_fields(user_id, [{"field_key": "insurance_limit", "field_value": "USD 1M"}])

    first_doc = save_company_document(user_id, "insurance.pdf", b"Policy version one", document_key="insurance")
    with_doc_v1 = get_company_profile(user_id)
    v1_hash = compute_profile_hash(with_doc_v1)
    assert v1_hash != dynamic_hash
    second_doc = save_company_document(user_id, "insurance.pdf", b"Policy version two", document_key="insurance")
    with_doc_v2 = get_company_profile(user_id)
    assert second_doc != first_doc
    assert next(d for d in with_doc_v2["supporting_documents"] if d["id"] == second_doc)["version"] == 2
    v2_hash = compute_profile_hash(with_doc_v2)
    assert v2_hash != v1_hash

    save_company_document_values(user_id, second_doc, [{
        "value_key": "limit", "value_text": "USD 1M", "source_excerpt": "Limit: USD 1M",
        "source_page": 2, "verification_status": "unverified",
    }])
    before_verify = compute_profile_hash(get_company_profile(user_id))
    value_id = get_company_documents(user_id)[0]["extracted_values"][0]["id"]
    update_company_document_value_status(user_id, value_id, "verified")
    verified_hash = compute_profile_hash(get_company_profile(user_id))
    assert verified_hash != before_verify

    saved = get_connection()
    try:
        saved_row = saved.execute("SELECT profile_hash FROM tender_analyses WHERE tender_id = ? ORDER BY id DESC LIMIT 1",
                                  (tender_id,)).fetchone()
        assert saved_row["profile_hash"] == initial_hash
    finally:
        saved.close()
    assert compute_profile_hash(get_company_profile(user_id)) != saved_row["profile_hash"]


def test_invalid_verification_status_is_rejected(users):
    user_id, _ = users
    doc_id = save_company_document(user_id, "status.pdf", b"Status test")
    with pytest.raises(ValueError, match="Unsupported verification status"):
        save_company_document_values(user_id, doc_id, [{
            "value_key": "x", "value_text": "y", "verification_status": "guessed",
        }])


def test_additive_migration_preserves_existing_profile_rows(monkeypatch, tmp_path):
    legacy_db = tmp_path / "legacy_profile.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute("""
        CREATE TABLE company_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, company_name TEXT DEFAULT '',
            industry TEXT DEFAULT '', years_of_experience TEXT DEFAULT '', annual_turnover TEXT DEFAULT '',
            certifications TEXT DEFAULT '', technical_capabilities TEXT DEFAULT '', past_projects TEXT DEFAULT '',
            financial_details TEXT DEFAULT '', contact_name TEXT DEFAULT '', contact_email TEXT DEFAULT '',
            contact_phone TEXT DEFAULT '', contact_address TEXT DEFAULT '', updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO company_profile (user_id, company_name, annual_turnover) VALUES (7, 'Legacy Co', 'INR 2 crore')")
    conn.commit()
    conn.close()

    monkeypatch.setattr("database.DB_PATH", str(legacy_db))
    init_db()
    init_db()  # migration remains safe when app startup calls it again
    conn = get_connection()
    try:
        row = conn.execute("SELECT user_id, company_name, annual_turnover FROM company_profile").fetchone()
        assert tuple(row) == (7, "Legacy Co", "INR 2 crore")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"company_profile_fields", "company_documents", "company_document_values"} <= tables
    finally:
        conn.close()
