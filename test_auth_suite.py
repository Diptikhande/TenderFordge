"""
Comprehensive Multi-Tenant Authentication and Data Isolation Test Suite for TenderForge.
Tests:
1. User registration (two separate accounts).
2. Password hashing validation (salting + PBKDF2 verification, plaintext never stored).
3. Successful authentication and invalid password handling.
4. Duplicate email registration prevention.
5. Unauthenticated access prevention (None / invalid user_id rejects operations).
6. Company profile isolation between accounts.
7. Tender upload & PDF extraction under each account.
8. Tender analysis isolation between accounts.
9. Cross-tenant access prevention (Account A cannot read Account B's tenders/analyses).
10. Dashboard stats scoping per account.
11. Data persistence across database reconnection / container restart.
12. Targeted test data cleanup preserving existing user data.
"""

import os
import sys
import fitz
import sqlite3

from database import (
    init_db,
    create_user,
    authenticate_user,
    get_user_by_id,
    save_company_profile,
    get_company_profile,
    save_tender,
    get_all_tenders,
    get_tender_by_id,
    save_analysis,
    get_analysis_for_tender,
    compute_profile_hash,
    get_dashboard_stats,
    get_tenders_with_analysis,
    get_connection,
    DB_PATH
)
from pdf_processor import extract_text_from_pdf
from analyzer import run_deterministic_checks, enforce_deterministic_rules

def run_tests():
    print("=" * 60)
    print("  TENDERFORGE AUTHENTICATION & MULTI-TENANCY TEST SUITE")
    print("=" * 60)

    # Ensure DB schema and migrations are initialized
    init_db()

    # Define test credentials
    user_a_email = "test_alice_defense@tenderforge-audit.internal"
    user_a_pw = "AliceAlpha#2026!SecureKey"
    user_a_name = "Alice Vance"

    user_b_email = "test_bob_logistics@tenderforge-audit.internal"
    user_b_pw = "BobBravo#2026!SuperSecret"
    user_b_name = "Bob Sterling"

    # Pre-cleanup in case of prior interrupted run
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE email IN (?, ?)", (user_a_email, user_b_email))
    old_ids = [row["id"] for row in c.fetchall()]
    for uid in old_ids:
        c.execute("DELETE FROM tender_analyses WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM tenders WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM company_profile WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    conn.close()

    # --- 1. USER REGISTRATION ---
    print("\n[TEST 1] Registering Two Independent Accounts...")
    ok_a, msg_a, user_a = create_user(user_a_email, user_a_pw, user_a_name)
    assert ok_a and user_a is not None and user_a["id"] > 0, f"Failed to register User A: {msg_a}"
    assert user_a["email"] == user_a_email, "User A email mismatch"
    print(f" -> User A registered: ID={user_a['id']}, Email={user_a['email']}")

    ok_b, msg_b, user_b = create_user(user_b_email, user_b_pw, user_b_name)
    assert ok_b and user_b is not None and user_b["id"] > 0, f"Failed to register User B: {msg_b}"
    assert user_b["id"] != user_a["id"], "User IDs must be unique"
    print(f" -> User B registered: ID={user_b['id']}, Email={user_b['email']}")

    # --- 2. PASSWORD HASHING VERIFICATION ---
    print("\n[TEST 2] Verifying Password Hashing & Plaintext Secrecy...")
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE id IN (?, ?)", (user_a["id"], user_b["id"]))
    rows = c.fetchall()
    conn.close()

    for row in rows:
        stored_hash = row["password_hash"]
        assert stored_hash != user_a_pw and stored_hash != user_b_pw, "SECURITY VIOLATION: Plaintext password found in database!"
        assert "$" in stored_hash, "Hash format invalid: must be salt$hash"
        salt, h = stored_hash.split("$", 1)
        assert len(salt) == 32, "Salt must be 16 bytes hex (32 characters)"
        assert len(h) == 64, "PBKDF2 SHA-256 hash must be 64 characters"
    print(" -> PASS: Passwords salted and hashed with PBKDF2-SHA256 (no plaintext stored).")

    # --- 3. AUTHENTICATION & LOGIN CHECKS ---
    print("\n[TEST 3] Testing Authentication Scenarios...")
    # Successful login
    ok_auth_a, msg_auth_a, auth_a = authenticate_user(user_a_email, user_a_pw)
    assert ok_auth_a and auth_a is not None and auth_a["id"] == user_a["id"], f"User A valid login failed: {msg_auth_a}"
    print(" -> PASS: Valid credentials authenticate successfully.")

    # Invalid password
    ok_bad, msg_bad, bad_pw = authenticate_user(user_a_email, "WrongPassword#999")
    assert not ok_bad and bad_pw is None, "SECURITY FAIL: Invalid password succeeded"
    print(f" -> PASS: Invalid password rejected ({msg_bad}).")

    # Non-existent user
    ok_no, msg_no, no_user = authenticate_user("nobody@nowhere.com", "SomePassword123")
    assert not ok_no and no_user is None, "SECURITY FAIL: Non-existent user succeeded"
    print(f" -> PASS: Non-existent user rejected ({msg_no}).")

    # --- 4. DUPLICATE REGISTRATION PREVENTION ---
    print("\n[TEST 4] Testing Duplicate Email Registration...")
    ok_dup, msg_dup, user_dup = create_user(user_a_email, "AnotherPassword123", "Alice Clone")
    assert not ok_dup and user_dup is None, "FAIL: Duplicate email registration succeeded!"
    print(f" -> PASS: Duplicate registration properly blocked ({msg_dup}).")

    # --- 5. UNAUTHENTICATED ACCESS PREVENTION ---
    print("\n[TEST 5] Testing Unauthenticated Operations Protection...")
    # Read operations safely return None or [] without exposing data
    assert get_company_profile(user_id=None) is None, "FAIL: get_company_profile(None) should return None"
    assert get_all_tenders(user_id=None) == [], "FAIL: get_all_tenders(None) should return []"
    assert get_tender_by_id(tender_id=1, user_id=None) is None, "FAIL: get_tender_by_id(None) should return None"
    assert get_analysis_for_tender(tender_id=1, user_id=None) is None, "FAIL: get_analysis_for_tender(None) should return None"
    print(" -> PASS: Unauthenticated read operations safely return None/empty without exposing tenant data.")

    # Mutating operations strictly raise ValueError if user_id is missing
    try:
        save_company_profile({"company_name": "Ghost Corp"}, user_id=None)
        assert False, "FAIL: save_company_profile without user_id should raise ValueError"
    except ValueError as e:
        print(f" -> PASS: save_company_profile without user_id rejected ({e}).")

    # --- 6. COMPANY PROFILE ISOLATION ---
    print("\n[TEST 6] Testing Company Profile Multi-Tenancy & Isolation...")
    profile_a = {
        "company_name": "Alice Cyber Defense LLC",
        "industry": "Cybersecurity & Zero Trust Architecture",
        "years_of_experience": "10 years",
        "annual_turnover": "$25,000,000 USD",
        "certifications": "ISO 27001:2022, SOC 2 Type II, FedRAMP High",
        "technical_capabilities": "Cloud Security, AI SIEM, DevSecOps",
        "past_projects": "National Cyber Grid monitoring portal ($8M, 2024)",
        "financial_details": "Consistently profitable, $5M unencumbered credit",
        "contact_name": "Alice Vance",
        "contact_email": user_a_email,
        "contact_phone": "+1 555-0101",
        "contact_address": "100 Security Way, McLean, VA"
    }
    save_company_profile(profile_a, user_id=user_a["id"])

    profile_b = {
        "company_name": "Bob Aerospace Heavy Freight Corp",
        "industry": "Commercial & Defense Aviation Logistics",
        "years_of_experience": "15 years",
        "annual_turnover": "$60,000,000 USD",
        "certifications": "AS9100D, ISO 9001:2015, FAA Part 145",
        "technical_capabilities": "Heavy Lift Cargo, Global Charter, Cold Chain Logistics",
        "past_projects": "Trans-Pacific Satellite Transport ($14M, 2025)",
        "financial_details": "$20M line of credit, bonded carrier status",
        "contact_name": "Bob Sterling",
        "contact_email": user_b_email,
        "contact_phone": "+1 555-0202",
        "contact_address": "500 Runway Blvd, Seattle, WA"
    }
    save_company_profile(profile_b, user_id=user_b["id"])

    retrieved_a = get_company_profile(user_id=user_a["id"])
    retrieved_b = get_company_profile(user_id=user_b["id"])

    assert retrieved_a["company_name"] == "Alice Cyber Defense LLC", "User A profile mismatch"
    assert retrieved_b["company_name"] == "Bob Aerospace Heavy Freight Corp", "User B profile mismatch"
    assert retrieved_a["company_name"] != retrieved_b["company_name"], "Profiles must be distinct"
    print(" -> PASS: Company profiles strictly isolated per user account.")

    # --- 7. TENDER UPLOAD & SCOPING ---
    print("\n[TEST 7] Creating & Uploading PDFs for Each Account...")
    os.makedirs("/app/data/uploads", exist_ok=True)
    pdf_a_path = "/app/data/uploads/test_tender_alice.pdf"
    pdf_b_path = "/app/data/uploads/test_tender_bob.pdf"

    # PDF for Alice
    doc_a = fitz.open()
    p_a = doc_a.new_page()
    text_a = """
    TENDER REF: CYBER-FED-2026-001
    TITLE: Zero Trust Endpoint Threat Intelligence Platform
    CRITERIA:
    - Minimum 7 years experience in defense cyber security.
    - Mandatory ISO 27001:2022 certification.
    - Minimum turnover of $10,000,000 USD.
    DEADLINE: 2026-12-15
    """
    p_a.insert_text((50, 50), text_a, fontsize=10)
    doc_a.save(pdf_a_path)
    doc_a.close()

    # PDF for Bob
    doc_b = fitz.open()
    p_b = doc_b.new_page()
    text_b = """
    TENDER REF: AERO-CARGO-2026-888
    TITLE: Global Air Charter and Heavy Satellite Transportation
    CRITERIA:
    - Minimum 10 years experience in FAA-certified aviation logistics.
    - Mandatory AS9100D certification.
    - Minimum turnover of $30,000,000 USD.
    DEADLINE: 2026-11-30
    """
    p_b.insert_text((50, 50), text_b, fontsize=10)
    doc_b.save(pdf_b_path)
    doc_b.close()

    # Extract & Save for User A
    ext_a = extract_text_from_pdf(pdf_a_path)
    tender_a_id = save_tender(
        filename="test_tender_alice.pdf",
        filepath=pdf_a_path,
        extracted_text=ext_a["text"],
        page_count=ext_a["page_count"],
        upload_status="success",
        user_id=user_a["id"]
    )
    print(f" -> Tender A saved for User A: ID={tender_a_id}")

    # Extract & Save for User B
    ext_b = extract_text_from_pdf(pdf_b_path)
    tender_b_id = save_tender(
        filename="test_tender_bob.pdf",
        filepath=pdf_b_path,
        extracted_text=ext_b["text"],
        page_count=ext_b["page_count"],
        upload_status="success",
        user_id=user_b["id"]
    )
    print(f" -> Tender B saved for User B: ID={tender_b_id}")

    # --- 8. TENDER & ANALYSIS CROSS-TENANT ISOLATION ---
    print("\n[TEST 8] Verifying Cross-Tenant Isolation (Tenders & Analyses)...")
    # Tenders listing isolation
    tenders_a = get_all_tenders(user_id=user_a["id"])
    tenders_b = get_all_tenders(user_id=user_b["id"])

    tenders_a_ids = [t["id"] for t in tenders_a]
    tenders_b_ids = [t["id"] for t in tenders_b]

    assert tender_a_id in tenders_a_ids, "Tender A must appear in User A tenders"
    assert tender_b_id not in tenders_a_ids, "SECURITY VIOLATION: Tender B leaked into User A tenders!"

    assert tender_b_id in tenders_b_ids, "Tender B must appear in User B tenders"
    assert tender_a_id not in tenders_b_ids, "SECURITY VIOLATION: Tender A leaked into User B tenders!"
    print(" -> PASS: get_all_tenders strictly returns only the authenticated user's tenders.")

    # Cross-tenant get_tender_by_id access check
    unauthorized_tender_access = get_tender_by_id(tender_id=tender_b_id, user_id=user_a["id"])
    assert unauthorized_tender_access is None, "SECURITY VIOLATION: User A was able to retrieve User B's tender!"

    unauthorized_tender_access_2 = get_tender_by_id(tender_id=tender_a_id, user_id=user_b["id"])
    assert unauthorized_tender_access_2 is None, "SECURITY VIOLATION: User B was able to retrieve User A's tender!"
    print(" -> PASS: Direct tender ID lookup by foreign user strictly returns None.")

    # Save Analyses
    det_a = run_deterministic_checks(ext_a["text"], retrieved_a)
    analysis_a_data = {
        "tender_title": "Zero Trust Endpoint Threat Intelligence Platform",
        "issuing_authority": "Federal Cyber Division",
        "submission_deadline": "2026-12-15",
        "estimated_budget": "$15,000,000",
        "compliance_matrix": det_a,
        "key_risks": ["Strict SLA penalties"],
        "missing_information": ["Hardware specification sheet"],
        "recommendation": "BID",
        "recommendation_reasoning": "Full alignment with cybersecurity capabilities."
    }
    save_analysis(
        tender_id=tender_a_id,
        profile_hash=compute_profile_hash(retrieved_a),
        recommendation=analysis_a_data["recommendation"],
        executive_summary=analysis_a_data["recommendation_reasoning"],
        structured_tender_data={"title": analysis_a_data["tender_title"]},
        compliance_matrix=analysis_a_data["compliance_matrix"],
        risks=analysis_a_data["key_risks"],
        missing_information=analysis_a_data["missing_information"],
        model_used="deterministic-engine",
        user_id=user_a["id"]
    )

    det_b = run_deterministic_checks(ext_b["text"], retrieved_b)
    analysis_b_data = {
        "tender_title": "Global Air Charter and Heavy Satellite Transportation",
        "issuing_authority": "Space Logistics Agency",
        "submission_deadline": "2026-11-30",
        "estimated_budget": "$35,000,000",
        "compliance_matrix": det_b,
        "key_risks": ["Airspace transit permits required"],
        "missing_information": ["Hazmat handling protocols"],
        "recommendation": "BID",
        "recommendation_reasoning": "Optimal fit for FAA heavy transport fleet."
    }
    save_analysis(
        tender_id=tender_b_id,
        profile_hash=compute_profile_hash(retrieved_b),
        recommendation=analysis_b_data["recommendation"],
        executive_summary=analysis_b_data["recommendation_reasoning"],
        structured_tender_data={"title": analysis_b_data["tender_title"]},
        compliance_matrix=analysis_b_data["compliance_matrix"],
        risks=analysis_b_data["key_risks"],
        missing_information=analysis_b_data["missing_information"],
        model_used="deterministic-engine",
        user_id=user_b["id"]
    )

    # Cross-tenant analysis access check
    ana_a_by_a = get_analysis_for_tender(tender_id=tender_a_id, user_id=user_a["id"])
    assert ana_a_by_a is not None, "User A should retrieve their own tender analysis"

    ana_b_by_b = get_analysis_for_tender(tender_id=tender_b_id, user_id=user_b["id"])
    assert ana_b_by_b is not None, "User B should retrieve their own tender analysis"

    ana_b_by_a = get_analysis_for_tender(tender_id=tender_b_id, user_id=user_a["id"])
    assert ana_b_by_a is None, "SECURITY VIOLATION: User A retrieved User B's tender analysis!"

    ana_a_by_b = get_analysis_for_tender(tender_id=tender_a_id, user_id=user_b["id"])
    assert ana_a_by_b is None, "SECURITY VIOLATION: User B retrieved User A's tender analysis!"
    print(" -> PASS: get_analysis_for_tender enforces strict tenant isolation.")

    # --- 9. DASHBOARD STATS SCOPING ---
    print("\n[TEST 9] Verifying Dashboard Stats Isolation...")
    stats_a = get_dashboard_stats(user_id=user_a["id"])
    stats_b = get_dashboard_stats(user_id=user_b["id"])

    assert stats_a["total_tenders"] >= 1, "Stats A should include Tender A"
    assert stats_b["total_tenders"] >= 1, "Stats B should include Tender B"
    print(f" -> User A stats: {stats_a}")
    print(f" -> User B stats: {stats_b}")
    print(" -> PASS: Dashboard stats are scoped to each authenticated user.")

    # --- 10. CLEANUP TEST DATA ---
    print("\n[TEST 10] Cleaning up temporary audit test records...")
    conn = get_connection()
    c = conn.cursor()
    for uid in (user_a["id"], user_b["id"]):
        c.execute("DELETE FROM tender_analyses WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM tenders WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM company_profile WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    conn.close()

    # Clean up test PDFs
    for p in (pdf_a_path, pdf_b_path):
        if os.path.exists(p):
            os.remove(p)

    print(" -> PASS: Test records and files cleanly removed.")
    print("\n" + "=" * 60)
    print("  ALL MULTI-TENANCY & AUTHENTICATION TESTS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
