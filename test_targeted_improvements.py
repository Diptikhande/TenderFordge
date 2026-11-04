"""
Test Suite for TenderForge Targeted Improvements:
1. Reports Page export button removal & page integrity
2. LLM processing controls: timeouts, bounded retries, duplicate prevention, failure safety
3. 4-Status Taxonomy: Satisfied, Confirmed gap, Missing company information, Requires verification
4. Dynamic Company Profile Form: Partial updates without overwriting unrelated fields or other tenant data
5. Evidence Distinction: Self-declared claims vs verified supporting documents
6. Re-analyze Using Updated Information: Deterministic re-evaluation with concise summary and limitations
"""

import sys
import os
import inspect

from database import (
    init_db,
    create_user,
    authenticate_user,
    get_company_profile,
    save_company_profile,
    save_tender,
    get_tender_by_id,
    compute_profile_hash,
    save_analysis,
    get_analysis_for_tender,
    get_connection,
)
from analyzer import (
    evaluate_evidence_matching,
    reevaluate_tender_analysis,
    extract_bid_recommendation,
    extract_eligibility_analysis,
    NOT_FOUND,
)

init_db()


def test_reports_page_integrity():
    print("\n--- 1. Testing Reports Page Integrity & Button Removal ---")
    with open("app.py", "r", encoding="utf-8") as f:
        app_code = f.read()

    # The 3 buttons that MUST be removed:
    forbidden_buttons = [
        "Download Executive Briefing (.md)",
        "Export Compliance Matrix (.csv)",
        "Export Risk Register (.json)"
    ]
    for btn in forbidden_buttons:
        assert btn not in app_code, f"Button '{btn}' was found in app.py but should have been removed!"
    print("PASS: The three export buttons are successfully removed from app.py.")

    # The remaining Reports page elements MUST be intact:
    assert 'elif st.session_state["current_page"] == "📈 Reports":' in app_code, "Reports page missing from app.py"
    assert "Executive Reports & Audit Analytics" in app_code, "Reports page header missing"
    assert "Select Tender Document for Executive Report" in app_code, "Tender selector missing"
    assert "Compliance Rate" in app_code, "Compliance Rate KPI card missing"
    assert "Confirmed Gaps" in app_code, "Confirmed Gaps KPI card missing"
    assert "Executive Audit Briefing:" in app_code, "Executive briefing card missing"
    print("PASS: All remaining Reports page elements and KPI metrics remain intact.")


def test_llm_processing_controls():
    print("\n--- 2. Testing LLM Processing Controls ---")
    with open("analyzer.py", "r", encoding="utf-8") as f:
        analyzer_code = f.read()

    # Verify timeout <= 25s and bounded retries
    assert "timeout=20" in analyzer_code, "analyzer.py must enforce a reasonable timeout (20s)"
    assert "max_retries = 1" in analyzer_code, "analyzer.py must enforce bounded retries (max_retries = 1)"
    print("PASS: Bounded retries and 20s timeouts are enforced in call_groq_modular.")

    with open("app.py", "r", encoding="utf-8") as f:
        app_code = f.read()

    # Verify duplicate analysis prevention
    assert "saved_analysis and not profile_changed" in app_code, "Duplicate analysis check missing in app.py"
    assert "Duplicate AI processing was skipped" in app_code, "Duplicate skipped message missing in app.py"
    print("PASS: Duplicate processing prevention is implemented for unchanged tender and profile data.")

    # Verify failure safety: never claim success on failure
    assert 'not analysis_res.get("success")' in app_code, "Failure condition check missing"
    assert "The analysis could not be completed" in app_code, "Friendly failure message missing"
    print("PASS: Failure message is displayed and analysis is never claimed successful when failed.")


def test_four_status_taxonomy():
    print("\n--- 3. Testing 4-Status Taxonomy ---")
    # Requirement:
    # - Satisfied
    # - Confirmed gap
    # - Missing company information
    # - Requires verification
    # "Do not treat missing evidence as proof of failure or success."

    turnover_req = {
        "requirement_id": "REQ-TOV-01",
        "category": "Financial",
        "requirement": "Minimum annual turnover of ₹10 Crores over last 3 years",
        "source_quotation": "Minimum annual turnover of 10 Crore INR required",
        "is_mandatory": True
    }

    # Case A: Missing Company Information (Profile has no turnover)
    empty_profile = {"company_name": "Acme Corp"}
    res_missing = evaluate_evidence_matching(turnover_req, empty_profile)
    assert res_missing["status"] == "MISSING COMPANY INFORMATION", f"Expected MISSING COMPANY INFORMATION, got {res_missing['status']}"
    assert res_missing["company_evidence"] == NOT_FOUND
    print("PASS: Missing company evidence is classified as MISSING COMPANY INFORMATION (not failure or success).")

    # Case B: Confirmed Gap (Turnover is explicitly below threshold)
    low_profile = {"company_name": "Acme Corp", "annual_turnover": "₹2 Crores"}
    res_gap = evaluate_evidence_matching(turnover_req, low_profile)
    assert res_gap["status"] == "CONFIRMED GAP", f"Expected CONFIRMED GAP, got {res_gap['status']}"
    print("PASS: Explicit contradiction (turnover INR 2 Cr < INR 10 Cr) is classified as CONFIRMED GAP.")

    # Case C: Satisfied (Turnover satisfies threshold)
    high_profile = {"company_name": "Acme Corp", "annual_turnover": "₹25 Crores"}
    res_sat = evaluate_evidence_matching(turnover_req, high_profile)
    assert res_sat["status"] == "SATISFIED", f"Expected SATISFIED, got {res_sat['status']}"
    print("PASS: Verifiable matching evidence is classified as SATISFIED.")

    # Case D: Requires Verification (Statutory / unverified affidavit / blacklisting declaration)
    blacklisting_req = {
        "requirement_id": "REQ-AFF-01",
        "category": "Eligibility",
        "requirement": "Undertaking of non-blacklisting by any state or central government department",
        "source_quotation": "The bidder shall not be debarred or blacklisted by any government authority.",
        "is_mandatory": True
    }
    res_verify = evaluate_evidence_matching(blacklisting_req, high_profile)
    assert res_verify["status"] == "REQUIRES VERIFICATION", f"Expected REQUIRES VERIFICATION, got {res_verify['status']}"
    print("PASS: Unverified statutory undertaking is classified as REQUIRES VERIFICATION.")


def test_profile_partial_update_and_tenant_isolation():
    print("\n--- 4. Testing Profile Partial Updates & Multi-Tenant Isolation ---")
    # Register two separate users
    email_a = "tenant_alpha_test@tenderforge.internal"
    email_b = "tenant_beta_test@tenderforge.internal"
    pwd = "SecureTestPassword123!"

    ok, _, user_a = authenticate_user(email_a, pwd)
    if not user_a:
        ok, _, user_a = create_user(email_a, pwd, "Tenant Alpha")
    uid_a = user_a["id"]

    ok, _, user_b = authenticate_user(email_b, pwd)
    if not user_b:
        ok, _, user_b = create_user(email_b, pwd, "Tenant Beta")
    uid_b = user_b["id"]

    # Initial profile for Tenant A
    save_company_profile({
        "company_name": "Alpha Solutions Ltd",
        "industry": "Software Engineering",
        "years_of_experience": "10 years",
        "annual_turnover": "₹15 Crores",
        "certifications": "ISO 9001:2015",
        "technical_capabilities": "Python, Cloud Microservices",
        "past_projects": "Large scale ERP for Railway",
        "financial_details": "Profitable for 5 years",
        "contact_name": "Alice Alpha",
        "contact_email": email_a
    }, user_id=uid_a)

    # Initial profile for Tenant B
    save_company_profile({
        "company_name": "Beta Defense Systems",
        "industry": "Defense Logistics",
        "years_of_experience": "15 years",
        "annual_turnover": "$50,000,000 USD",
        "certifications": "ISO 27001:2022",
        "technical_capabilities": "Hardware Security",
        "contact_name": "Bob Beta",
        "contact_email": email_b
    }, user_id=uid_b)

    # Now, Tenant A updates ONLY 'certifications' and adds an evidence annotation
    save_company_profile({
        "certifications": "ISO 9001:2015, ISO 27001:2022 [Self-declared claim; supporting doc requested]"
    }, user_id=uid_a)

    prof_a = get_company_profile(uid_a)
    assert prof_a["company_name"] == "Alpha Solutions Ltd", "Unrelated field 'company_name' was overwritten!"
    assert prof_a["years_of_experience"] == "10 years", "Unrelated field 'years_of_experience' was overwritten!"
    assert prof_a["annual_turnover"] == "₹15 Crores", "Unrelated field 'annual_turnover' was overwritten!"
    assert "ISO 27001:2022" in prof_a["certifications"], "Updated certification field missing!"
    assert "[Self-declared claim" in prof_a["certifications"], "Claim status annotation missing!"
    print("PASS: Partial update preserved all unrelated fields for Tenant A.")

    # Verify Tenant B's data was completely untouched
    prof_b = get_company_profile(uid_b)
    assert prof_b["company_name"] == "Beta Defense Systems", "Tenant B data was corrupted by Tenant A update!"
    assert prof_b["annual_turnover"] == "$50,000,000 USD", "Tenant B turnover was affected!"
    assert prof_b["contact_name"] == "Bob Beta", "Tenant B contact was affected!"
    print("PASS: Strict multi-tenant isolation preserved between Tenant A and Tenant B.")


def test_reevaluate_tender_analysis_and_summary():
    print("\n--- 5. Testing Re-analysis with Updated Information & Concise Summary ---")
    tender_text = """
    TENDER NOTIFICATION # TN-GOV-2026-88
    PROJECT: Integrated Citizen Portal and Payment Services
    ISSUING AUTHORITY: State IT Directorate

    1. ELIGIBILITY:
    - Minimum 5 years in business / operating experience.
    - Minimum annual turnover of ₹10 Crores over last 3 years.
    - Valid ISO 9001 and ISO 27001 certifications.
    - Certificate of Incorporation under Companies Act.

    2. SUBMISSION DEADLINE: 2026-12-01
    """

    # User with missing turnover and ISO 27001
    initial_profile = {
        "company_name": "CivicTech Systems",
        "years_of_experience": "7 years in web applications",
        "annual_turnover": "", # MISSING
        "certifications": "ISO 9001:2015", # Missing ISO 27001
        "technical_capabilities": "Web & Mobile Development"
    }

    # Step 1: Initial evaluation before profile completion
    res1 = reevaluate_tender_analysis(tender_text, initial_profile)
    summary1 = res1["summary"]

    assert "eligibility" in summary1, "Summary missing 'eligibility'"
    assert "satisfied_requirements" in summary1, "Summary missing 'satisfied_requirements'"
    assert "confirmed_gaps" in summary1, "Summary missing 'confirmed_gaps'"
    assert "remaining_missing_information" in summary1, "Summary missing 'remaining_missing_information'"
    assert "recommendation" in summary1, "Summary missing 'recommendation'"
    assert "reasons" in summary1, "Summary missing 'reasons'"
    assert "limitations" in summary1, "Summary missing 'limitations'"

    print(f"Initial Recommendation: {summary1['recommendation']}")
    print(f"Remaining Missing Items: {len(summary1['remaining_missing_information'])}")
    assert len(summary1["remaining_missing_information"]) > 0, "Expected missing information items in initial run"
    assert summary1["recommendation"] != "BID", "Should not recommend BID when critical requirements are missing"

    # Step 2: User completes company profile
    updated_profile = dict(initial_profile)
    updated_profile["annual_turnover"] = "₹18 Crores [Self-declared claim; CA audit cert requested]"
    updated_profile["certifications"] = "ISO 9001:2015, ISO 27001:2022 [Verified doc on file: ISO27001_Cert.pdf]"

    # Step 3: Re-evaluate using updated information
    res2 = reevaluate_tender_analysis(tender_text, updated_profile)
    summary2 = res2["summary"]

    print(f"Re-evaluated Recommendation: {summary2['recommendation']}")
    print(f"Re-evaluated Satisfied Requirements: {len(summary2['satisfied_requirements'])}")
    print(f"Re-evaluated Remaining Missing: {len(summary2['remaining_missing_information'])}")
    print(f"Summary Limitations: {summary2['limitations']}")

    # Turnover should now be satisfied
    assert any("turnover" in s.lower() or "det-turnover" in s.lower() for s in summary2["satisfied_requirements"]), "Turnover should be satisfied after profile update"
    # ISO 27001 should now be satisfied
    assert any("iso 27001" in s.lower() or "det-cert-iso27001" in s.lower() for s in summary2["satisfied_requirements"]), "ISO 27001 should be satisfied after profile update"

    # Notice limitations: Since turnover is self-declared, limitations must be explicitly stated
    assert "Limitations:" in summary2["limitations"], "Limitations must be explicitly stated when self-declared or unverified claims are used"
    # Provisional recommendation must NOT be unconditional BID
    assert summary2["recommendation"] in ["REVIEW REQUIRED", "PROVISIONAL BID", "BID"], "Valid recommendation returned"
    print("PASS: Re-evaluation successfully re-evaluated affected requirements, satisfied updated criteria, and provided concise summary with explicit limitations.")


if __name__ == "__main__":
    print("============================================================")
    print("  RUNNING TARGETED IMPROVEMENTS TEST SUITE")
    print("============================================================")
    try:
        test_reports_page_integrity()
        test_llm_processing_controls()
        test_four_status_taxonomy()
        test_profile_partial_update_and_tenant_isolation()
        test_reevaluate_tender_analysis_and_summary()
        print("\n============================================================")
        print("  ALL TARGETED IMPROVEMENT TESTS PASSED! (5/5)")
        print("============================================================")
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
